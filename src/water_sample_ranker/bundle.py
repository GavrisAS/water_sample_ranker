"""Verified bundle receipts and same-filesystem atomic, exclusive publication.

os.link uses POSIX link / Windows CreateHardLinkW. No rename/replace fallback:
an unsupported filesystem fails closed. fsync of directories is best effort
on platforms that cannot open directories; every payload is fsynced first.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class OwnedFile:
    path: Path
    device: int
    inode: int

    @classmethod
    def capture(cls, path: Path) -> OwnedFile:
        s = path.lstat()
        if path.is_symlink() or not s.st_ino:
            raise BundleError("FILE_OWNERSHIP_UNPROVABLE")
        return cls(path, s.st_dev, s.st_ino)

    @classmethod
    def from_descriptor(cls, path: Path, descriptor: int) -> OwnedFile:
        s = os.fstat(descriptor)
        if not s.st_ino:
            raise BundleError("FILE_OWNERSHIP_UNPROVABLE")
        return cls(path, s.st_dev, s.st_ino)

    def remove(self) -> None:
        try:
            s = self.path.lstat()
            if not self.path.is_symlink() and (s.st_dev, s.st_ino) == (self.device, self.inode):
                self.path.unlink()
        except FileNotFoundError:
            pass


def sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return  # Python cannot fsync an NTFS directory handle; no durability claim.
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def link_exclusive(source: Path, target: Path) -> OwnedFile:
    owned = OwnedFile.capture(source)
    published = OwnedFile(target, owned.device, owned.inode)
    try:
        os.link(source, target)
    except BaseException:
        published.remove()
        raise
    return published


def publish_json_exclusive(path: Path, payload: dict[str, Any]) -> OwnedFile:
    """Readers see either no name or the entire flushed JSON; never a prefix.

    The caller must use a local directory it controls. Cleanup verifies file
    identity and never sweeps crash leftovers or unrelated names.
    """
    data = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=".bundle-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    owned = OwnedFile.from_descriptor(temporary, fd)
    published = OwnedFile(path, owned.device, owned.inode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        link_exclusive(temporary, path)
        sync_directory(path.parent)
    except BaseException:
        published.remove()  # Also handles an interruption immediately after link().
        raise
    finally:
        owned.remove()
    return published


def file_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BundleError("BUNDLE_MISSING_FILE")
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise BundleError("BUNDLE_CHANGED_DURING_HASH")
    return {"name": path.name, "bytes": after.st_size, "sha256": digest}


def _json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise BundleError("BUNDLE_DUPLICATE_KEY")
            value[key] = item
        return value

    if path.is_symlink() or path.stat().st_size > 64 * 1024**2:
        raise BundleError("BUNDLE_JSON_TYPE_OR_SIZE")
    raw = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if not isinstance(raw, dict):
        raise BundleError("BUNDLE_JSON_OBJECT_REQUIRED")
    return raw


def verify_bundle(
    directory: Path,
    *,
    expected_date: str | None = None,
    expected_version: str | None = None,
    expected_sources: dict[str, Any] | None = None,
    expected_fields: list[str] | None = None,
) -> dict[str, Any]:
    """COMPLETE is derived from all verified files and binding, never name existence."""
    try:
        marker = _json(directory / "bundle-complete.json")
        if (
            set(marker)
            != {"schema", "status", "calculation_date", "version", "source_manifest", "outputs"}
            or marker["schema"] != 1
            or marker["status"] != "COMPLETE"
        ):
            raise BundleError("BUNDLE_SCHEMA")
        date.fromisoformat(marker["calculation_date"])
        if not isinstance(marker["version"], str) or not marker["version"]:
            raise BundleError("BUNDLE_VERSION")
        sources = marker["source_manifest"]
        if not isinstance(sources, dict) or not sources:
            raise BundleError("BUNDLE_SOURCES")
        if any(
            expected is not None and marker[key] != expected
            for key, expected in (
                ("calculation_date", expected_date),
                ("version", expected_version),
                ("source_manifest", expected_sources),
            )
        ):
            raise BundleError("BUNDLE_BINDING")
        outputs = marker["outputs"]
        if not isinstance(outputs, list) or not outputs:
            raise BundleError("BUNDLE_OUTPUTS")
        fields = [o["field"] for o in outputs]
        if len(set(fields)) != len(fields) or (
            expected_fields is not None and fields != expected_fields
        ):
            raise BundleError("BUNDLE_FIELDS")
        for output in outputs:
            if set(output) != {"field", "workbook", "report", "log"}:
                raise BundleError("BUNDLE_OUTPUT_SCHEMA")
            field = output["field"]
            if not isinstance(field, str) or not field or any(c in field for c in "/\\:"):
                raise BundleError("BUNDLE_FIELD_NAME")
            stem = f"Water_Sample_Ranking_{field}"
            for role, suffix in (
                ("workbook", ".xlsx"),
                ("report", ".load-report.json"),
                ("log", ".log"),
            ):
                record = output[role]
                name = stem + suffix
                if record["name"] != name or file_record(directory / name) != record:
                    raise BundleError("BUNDLE_CHECKSUM_OR_NAME")
            report = _json(directory / output["report"]["name"])
            if any(
                report.get(k) != marker[k]
                for k in ("version", "calculation_date", "source_manifest")
            ):
                raise BundleError("BUNDLE_REPORT_BINDING")
            if (
                report.get("available_fields") != fields
                or report.get("selected_field") != field
                or report.get("errors") != []
                or report.get("source_changed_count") != 0
                or report.get("output", {}).get("sha256") != output["workbook"]["sha256"]
                or report.get("output", {}).get("size") != output["workbook"]["bytes"]
                or report.get("rows_written") != len(report.get("rows", []))
            ):
                raise BundleError("BUNDLE_REPORT_CONTENT")
        return {"status": "COMPLETE", "marker": marker}
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        return {"status": "INCOMPLETE", "reason": "MARKER_OR_PAYLOAD_INVALID"}
