import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
