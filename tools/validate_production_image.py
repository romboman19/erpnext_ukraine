#!/usr/bin/env python3
"""Validate the immutable ERPNext Ukraine production image contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Path], CommandResult]


def run_command(command: Sequence[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{pyproject_path} has no project.version")
    return version


def parse_bench_versions(output: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            versions[fields[0]] = fields[1]
    return versions


def parse_site_apps(output: str, site: str) -> set[str]:
    payload = json.loads(output)
    if not isinstance(payload, dict) or not isinstance(payload.get(site), list):
        raise ValueError(f"list-apps returned no application list for {site}")
    return {str(app) for app in payload[site]}


def collect_source_errors(source_root: Path, contract: Mapping[str, Any], lock: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    versions = contract.get("application_versions", {})
    expected_version = versions.get("erpnext_ua") if isinstance(versions, dict) else None
    actual_version = read_project_version(source_root / "pyproject.toml")
    if actual_version != expected_version:
        errors.append(f"pyproject version {actual_version!r} does not match contract {expected_version!r}")

    locked_app = lock.get("apps", {}).get("erpnext_ua", {})
    if locked_app.get("version") != expected_version:
        errors.append("source lock erpnext_ua version does not match the image contract")

    expected_apps = _string_set(contract, "image_apps", errors)
    site_apps = _string_set(contract, "site_apps", errors)
    retired_apps = _string_set(contract, "retired_apps", errors)
    if expected_apps != site_apps:
        errors.append("image_apps and site_apps must be identical for the clean production profile")
    if expected_apps.intersection(retired_apps):
        errors.append("retired apps cannot be part of the production image")

    excluded_apps = contract.get("excluded_apps")
    if not isinstance(excluded_apps, dict) or not excluded_apps:
        errors.append("excluded_apps must document every deliberately omitted app")
    elif expected_apps.intersection(excluded_apps):
        errors.append("excluded apps cannot be part of the production image")

    digest = lock.get("base_image", {}).get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)):
        errors.append("base image must be locked to a sha256 digest")

    for name, app in lock.get("apps", {}).items():
        if name == "erpnext_ua":
            continue
        commit = app.get("commit", "") if isinstance(app, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
            errors.append(f"{name} must be locked to a 40-character commit")

    return errors


def collect_bench_errors(
    bench_root: Path,
    contract: Mapping[str, Any],
    *,
    site: str | None = None,
    commit: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: CommandRunner = run_command,
) -> list[str]:
    errors: list[str] = []
    expected_apps = _string_set(contract, "image_apps", errors)
    app_directory = bench_root / "apps"
    actual_apps = {path.name for path in app_directory.iterdir() if path.is_dir()}
    _compare_app_sets("image app directories", expected_apps, actual_apps, errors)

    apps_txt = bench_root / "sites" / "apps.txt"
    listed_apps = {line.strip() for line in apps_txt.read_text().splitlines() if line.strip()}
    _compare_app_sets("sites/apps.txt", expected_apps, listed_apps, errors)

    versions_result = runner(("bench", "version", "--format", "plain"), bench_root)
    if versions_result.returncode:
        errors.append(_command_error("bench version", versions_result))
    else:
        actual_versions = parse_bench_versions(versions_result.stdout)
        for app, expected_version in contract.get("application_versions", {}).items():
            if actual_versions.get(app) != expected_version:
                errors.append(
                    f"{app} version {actual_versions.get(app)!r} does not match contract {expected_version!r}"
                )

    bench_python = bench_root / "env" / "bin" / "python"
    pip_result = runner((str(bench_python), "-m", "pip", "check"), bench_root)
    if pip_result.returncode:
        errors.append(_command_error("bench virtualenv pip check", pip_result))

    resolved_commit = commit or _image_commit(contract, environ or os.environ)
    pattern = contract.get("build_metadata", {}).get("commit_pattern", "")
    if not resolved_commit or not re.fullmatch(str(pattern), resolved_commit):
        errors.append("image revision is missing or is not a 40-character lowercase git commit")

    if site:
        site_result = runner(("bench", "--site", site, "list-apps", "--format", "json"), bench_root)
        if site_result.returncode:
            errors.append(_command_error(f"list-apps for {site}", site_result))
        else:
            try:
                installed_apps = parse_site_apps(site_result.stdout, site)
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
            else:
                expected_site_apps = _string_set(contract, "site_apps", errors)
                _compare_app_sets(f"installed apps for {site}", expected_site_apps, installed_apps, errors)

    return errors


def _string_set(contract: Mapping[str, Any], key: str, errors: list[str]) -> set[str]:
    value = contract.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{key} must be a non-empty list of application names")
        return set()
    if len(value) != len(set(value)):
        errors.append(f"{key} contains duplicate application names")
    return set(value)


def _compare_app_sets(label: str, expected: set[str], actual: set[str], errors: list[str]) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(f"{label} is missing: {', '.join(missing)}")
    if unexpected:
        errors.append(f"{label} has unexpected apps: {', '.join(unexpected)}")


def _image_commit(contract: Mapping[str, Any], environ: Mapping[str, str]) -> str | None:
    variable = contract.get("build_metadata", {}).get("commit_environment_variable")
    return environ.get(str(variable)) if variable else None


def _command_error(label: str, result: CommandResult) -> str:
    detail = (result.stdout + "\n" + result.stderr).strip()
    return f"{label} failed: {detail or f'exit {result.returncode}'}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--bench-root", type=Path)
    parser.add_argument("--site")
    parser.add_argument("--commit")
    args = parser.parse_args(argv)
    if bool(args.source_root) == bool(args.bench_root):
        parser.error("select exactly one of --source-root or --bench-root")
    if args.site and not args.bench_root:
        parser.error("--site requires --bench-root")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    contract = load_json(args.contract)
    if args.source_root:
        lock = load_json(args.source_root / "deployment" / "production" / "source-lock.json")
        errors = collect_source_errors(args.source_root, contract, lock)
    else:
        errors = collect_bench_errors(
            args.bench_root,
            contract,
            site=args.site,
            commit=args.commit,
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Production image contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
