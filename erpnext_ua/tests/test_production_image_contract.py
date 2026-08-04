import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.install_locked_chromium import install_archive
from tools.validate_production_image import (
    CommandResult,
    collect_bench_errors,
    collect_source_errors,
    parse_bench_versions,
    parse_site_apps,
)

CONTRACT = {
    "application_versions": {
        "frappe": "16.26.3",
        "erpnext": "16.26.2",
        "erpnext_ua": "0.16.0",
        "print_designer": "1.6.5",
    },
    "image_apps": ["frappe", "erpnext", "erpnext_ua", "print_designer"],
    "site_apps": ["frappe", "erpnext", "erpnext_ua", "print_designer"],
    "retired_apps": ["ukrainian_integrations", "erpnext_consignment_and_commission"],
    "excluded_apps": {"flow": "dependency conflict"},
    "build_metadata": {
        "commit_environment_variable": "ERPNEXT_UA_IMAGE_COMMIT",
        "commit_pattern": "^[0-9a-f]{40}$",
    },
    "runtime_artifacts": {
        "print_designer_chromium": {
            "version": "133.0.6943.35",
            "executable": "chromium/chrome-linux/headless_shell",
        }
    },
}


class ProductionImageContractTest(unittest.TestCase):
    def test_parses_bench_and_site_output(self):
        versions = parse_bench_versions("frappe 16.26.3 ()\nerpnext_ua 0.16.0 ()\n")
        apps = parse_site_apps('{"uat.local": ["frappe", "erpnext_ua"]}', "uat.local")

        self.assertEqual(versions["erpnext_ua"], "0.16.0")
        self.assertEqual(apps, {"frappe", "erpnext_ua"})

    def test_source_contract_matches_project_and_lock(self):
        root = Path(__file__).resolve().parents[2]
        contract = json.loads((root / "deployment/production/image-contract.json").read_text())
        lock = json.loads((root / "deployment/production/source-lock.json").read_text())

        self.assertEqual(collect_source_errors(root, contract, lock), [])

    def test_bench_contract_rejects_legacy_app_and_dependency_conflict(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bench_root = Path(temporary_directory)
            expected_apps = set(CONTRACT["image_apps"])
            for app in expected_apps | {"ukrainian_integrations"}:
                (bench_root / "apps" / app).mkdir(parents=True, exist_ok=True)
            (bench_root / "sites").mkdir()
            (bench_root / "sites" / "apps.txt").write_text("\n".join(sorted(expected_apps)))
            (bench_root / "env" / "bin").mkdir(parents=True)
            self._create_chromium(bench_root)

            def runner(command, cwd):
                del cwd
                if command[-3:] == ("version", "--format", "plain"):
                    output = "\n".join(
                        f"{app} {version} ()" for app, version in CONTRACT["application_versions"].items()
                    )
                    return CommandResult(0, output, "")
                return CommandResult(1, "", "frappe requires Click~=8.3.1, but click 8.1.8 is installed")

            errors = collect_bench_errors(
                bench_root,
                CONTRACT,
                commit="a" * 40,
                runner=runner,
            )

        self.assertTrue(any("unexpected apps: ukrainian_integrations" in error for error in errors))
        self.assertTrue(any("Click~=8.3.1" in error for error in errors))

    def test_site_contract_rejects_flow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bench_root = Path(temporary_directory)
            expected_apps = set(CONTRACT["image_apps"])
            for app in expected_apps:
                (bench_root / "apps" / app).mkdir(parents=True, exist_ok=True)
            (bench_root / "sites").mkdir()
            (bench_root / "sites" / "apps.txt").write_text("\n".join(sorted(expected_apps)))
            (bench_root / "env" / "bin").mkdir(parents=True)
            self._create_chromium(bench_root)

            def runner(command, cwd):
                del cwd
                if command[-3:] == ("version", "--format", "plain"):
                    output = "\n".join(
                        f"{app} {version} ()" for app, version in CONTRACT["application_versions"].items()
                    )
                    return CommandResult(0, output, "")
                if "list-apps" in command:
                    apps = sorted(expected_apps | {"flow"})
                    return CommandResult(0, json.dumps({"uat.local": apps}), "")
                return CommandResult(0, "No broken requirements found.\n", "")

            errors = collect_bench_errors(
                bench_root,
                CONTRACT,
                site="uat.local",
                commit="b" * 40,
                runner=runner,
            )

        self.assertTrue(any("installed apps for uat.local has unexpected apps: flow" in error for error in errors))

    def test_bench_contract_rejects_missing_chromium(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bench_root = Path(temporary_directory)
            expected_apps = set(CONTRACT["image_apps"])
            for app in expected_apps:
                (bench_root / "apps" / app).mkdir(parents=True, exist_ok=True)
            (bench_root / "sites").mkdir()
            (bench_root / "sites" / "apps.txt").write_text("\n".join(sorted(expected_apps)))
            (bench_root / "env" / "bin").mkdir(parents=True)

            def runner(command, cwd):
                del cwd
                if command[-3:] == ("version", "--format", "plain"):
                    output = "\n".join(
                        f"{app} {version} ()" for app, version in CONTRACT["application_versions"].items()
                    )
                    return CommandResult(0, output, "")
                return CommandResult(0, "No broken requirements found.\n", "")

            errors = collect_bench_errors(
                bench_root,
                CONTRACT,
                commit="c" * 40,
                runner=runner,
            )

        self.assertTrue(any("print_designer_chromium executable is missing" in error for error in errors))

    def test_installs_checksum_locked_chromium_archive(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bench_root = root / "bench"
            bench_root.mkdir()
            archive_path = root / "chromium.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "chrome-headless-shell-linux64/chrome-headless-shell",
                    b"executable",
                )
                archive.writestr("chrome-headless-shell-linux64/resources.pak", b"resource")
            expected_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()

            executable = install_archive(archive_path, bench_root, expected_sha256)

            self.assertEqual(executable, bench_root / "chromium/chrome-linux/headless_shell")
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertTrue((executable.parent / "resources.pak").is_file())

    @staticmethod
    def _create_chromium(bench_root: Path) -> None:
        executable = bench_root / "chromium/chrome-linux/headless_shell"
        executable.parent.mkdir(parents=True)
        executable.write_text("test")
        executable.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
