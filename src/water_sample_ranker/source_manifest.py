"""Private run provenance and read-only input fingerprints."""

import hashlib
from pathlib import Path
from typing import Any


def fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def unchanged(before: dict[str, Any], path: Path) -> bool:
    return before == fingerprint(path)
