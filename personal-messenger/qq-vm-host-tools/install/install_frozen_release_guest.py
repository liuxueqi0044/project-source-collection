"""Install one validated PMAI release inside the dedicated Windows guest."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

STAGING = Path(r"C:\PMAI\data\release-media\r20260912-01-5b183cf1df554921a06d23c8e62c3ff6")
RELEASE_ROOT = Path(r"C:\PMAI\app\releases")
RELEASE_ID = "r20260912-01"
TARGET = RELEASE_ROOT / RELEASE_ID
INCOMING = RELEASE_ROOT / ("." + RELEASE_ID + ".incoming")
STATUS = Path(r"C:\PMAI\data\guest-local-release-install.json")
SCHEMA = "pmai-guest-local-release-v1"
INSTALL_SCHEMA = "pmai-guest-local-release-install-v1"
MAX_FILE_BYTES = 128 * 1024 * 1024
CHECKED_MODULES = (
    "messenger_ai/adapters/qq/vm_driver/guest_composer.py",
    "messenger_ai/adapters/qq/vm_driver/session_identity.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_relative(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("MANIFEST_PATH_INVALID")
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeError("MANIFEST_PATH_INVALID")
    return path


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _manifest() -> tuple[dict[str, object], list[tuple[Path, int, str]]]:
    manifest_path = STAGING / "manifest.json"
    if not manifest_path.is_file() or not _inside(STAGING, manifest_path):
        raise RuntimeError("MANIFEST_MISSING")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError("MANIFEST_INVALID") from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("release_id") != RELEASE_ID:
        raise RuntimeError("MANIFEST_IDENTITY_INVALID")
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("MANIFEST_FILES_INVALID")
    result: list[tuple[Path, int, str]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise RuntimeError("MANIFEST_FILE_INVALID")
        rel = _safe_relative(item.get("path"))
        key = rel.as_posix()
        sha = item.get("sha256")
        if key in seen or not isinstance(item.get("length"), int) or item["length"] < 0:
            raise RuntimeError("MANIFEST_FILE_INVALID")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            raise RuntimeError("MANIFEST_FILE_INVALID")
        seen.add(key)
        result.append((rel, int(item["length"]), sha.upper()))
    if sum(rel.suffix.lower() == ".whl" for rel, _length, _sha in result) != 1:
        raise RuntimeError("WHEEL_NOT_UNIQUE")
    return value, result


def _verify_files(root: Path, entries: list[tuple[Path, int, str]]) -> None:
    for rel, length, expected in entries:
        path = root / rel
        if not _inside(root, path) or not path.is_file():
            raise RuntimeError("RELEASE_FILE_MISSING")
        actual_length = path.stat().st_size
        if actual_length != length or actual_length > MAX_FILE_BYTES or _sha256(path) != expected:
            raise RuntimeError("RELEASE_FILE_HASH_MISMATCH")


def _status(value: dict[str, object]) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".guest-local-release-install-", suffix=".json", dir=str(STATUS.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, STATUS)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _verify_wheel_modules(wheel: Path, installed_root: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for name in CHECKED_MODULES:
            if name not in names:
                raise RuntimeError("WHEEL_MODULE_MISSING")
            expected[name] = hashlib.sha256(archive.read(name)).hexdigest().upper()
    for name, digest in expected.items():
        installed = installed_root / Path(name)
        if not installed.is_file() or _sha256(installed) != digest:
            raise RuntimeError("INSTALLED_MODULE_HASH_MISMATCH")
    return expected


def _assert_guest_identity() -> None:
    if os.environ.get("COMPUTERNAME", "").upper() != "PMAI-QQVM":
        raise RuntimeError("GUEST_COMPUTER_MISMATCH")
    if os.environ.get("USERNAME", "").lower() != "qqbot":
        raise RuntimeError("GUEST_USER_MISMATCH")
    if os.name != "nt" or bool(__import__("ctypes").windll.shell32.IsUserAnAdmin()):
        raise RuntimeError("GUEST_ADMIN_FORBIDDEN")


def main() -> int:
    report: dict[str, object] = {"schema": INSTALL_SCHEMA, "release_id": RELEASE_ID, "state": "running", "succeeded": False}
    try:
        _assert_guest_identity()
        manifest, entries = _manifest()
        report["manifest_sha256"] = _sha256(STAGING / "manifest.json")
        if TARGET.exists():
            raise RuntimeError("TARGET_ALREADY_EXISTS")
        if INCOMING.exists():
            raise RuntimeError("INCOMING_ALREADY_EXISTS")
        RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
        INCOMING.mkdir()
        shutil.copy2(STAGING / "manifest.json", INCOMING / "manifest.json")
        if _sha256(INCOMING / "manifest.json") != _sha256(STAGING / "manifest.json"):
            raise RuntimeError("MANIFEST_COPY_HASH_MISMATCH")
        for rel, _length, _sha in entries:
            destination = INCOMING / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGING / rel, destination)
        _verify_files(INCOMING, entries)
        wheel = next(INCOMING / rel for rel, _length, _sha in entries if rel.suffix.lower() == ".whl")
        completed = subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall", str(wheel)], check=False, capture_output=True)
        report.update({"pip_returncode": completed.returncode, "pip_stdout_bytes": len(completed.stdout or b""), "pip_stderr_bytes": len(completed.stderr or b"")})
        if completed.returncode != 0:
            raise RuntimeError("PIP_INSTALL_FAILED")
        report["module_hashes"] = _verify_wheel_modules(
            wheel, Path(sys.prefix) / "Lib" / "site-packages"
        )
        os.replace(INCOMING, TARGET)
        report.update({"state": "succeeded", "succeeded": True, "target": str(TARGET), "manifest": manifest})
        _status(report)
        return 0
    except Exception as exc:
        report.update({"state": "failed", "error_code": str(exc)})
        _status(report)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
