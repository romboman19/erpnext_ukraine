#!/usr/bin/env python3
"""Install a checksum-locked Print Designer Chromium runtime."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ARCHIVE_ROOT = "chrome-headless-shell-linux64"
ARCHIVE_EXECUTABLE = "chrome-headless-shell"
RUNTIME_DIRECTORY = "chrome-linux"
RUNTIME_EXECUTABLE = "headless_shell"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def download(url: str, destination: Path) -> None:
    if urlparse(url).scheme != "https":
        raise ValueError("Chromium URL must use HTTPS")

    request = urllib.request.Request(url, headers={"User-Agent": "erpnext-ua-image-builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_archive(archive_path: Path, bench_root: Path, expected_sha256: str) -> Path:
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ValueError("expected SHA-256 must contain 64 lowercase hexadecimal characters")

    actual_sha256 = sha256_file(archive_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"Chromium SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}")

    bench_root = bench_root.resolve()
    if not bench_root.is_dir():
        raise FileNotFoundError(f"bench root does not exist: {bench_root}")

    with tempfile.TemporaryDirectory(prefix="locked-chromium-", dir=bench_root) as temporary:
        temporary_root = Path(temporary)
        extracted_root = temporary_root / "extracted"
        extracted_root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _validate_members(archive, extracted_root)
            archive.extractall(extracted_root)

        archive_root = extracted_root / ARCHIVE_ROOT
        executable = archive_root / ARCHIVE_EXECUTABLE
        if not executable.is_file():
            raise RuntimeError(f"Chromium archive is missing {ARCHIVE_ROOT}/{ARCHIVE_EXECUTABLE}")

        staged_runtime = temporary_root / "chromium"
        staged_runtime.mkdir()
        runtime_directory = staged_runtime / RUNTIME_DIRECTORY
        archive_root.rename(runtime_directory)
        runtime_executable = runtime_directory / RUNTIME_EXECUTABLE
        executable = runtime_directory / ARCHIVE_EXECUTABLE
        executable.rename(runtime_executable)
        runtime_executable.chmod(runtime_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        destination = bench_root / "chromium"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(staged_runtime, destination)

    installed_executable = bench_root / "chromium" / RUNTIME_DIRECTORY / RUNTIME_EXECUTABLE
    if not installed_executable.is_file() or not os.access(installed_executable, os.X_OK):
        raise RuntimeError("installed Chromium executable is missing or not executable")
    return installed_executable


def _validate_members(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if not member_path.is_relative_to(destination):
            raise RuntimeError(f"unsafe Chromium archive member: {member.filename}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-root", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="locked-chromium-download-") as temporary:
        archive_path = Path(temporary) / "chromium.zip"
        download(args.url, archive_path)
        executable = install_archive(archive_path, args.bench_root, args.sha256)
    print(f"Locked Chromium installed: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
