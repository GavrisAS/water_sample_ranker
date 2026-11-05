"""Scan the fresh Windows folder before any manual or automatic artifact upload."""

import argparse
from pathlib import Path

from scripts import verify_handoff as policy
from scripts.release_assets import APP, check_onedir


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--source-sha", required=True)
    args = p.parse_args()
    folder = args.binary.resolve().parent
    files = {}
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("CANDIDATE_SYMLINK")
        if path.is_file():
            files[path.relative_to(folder).as_posix()] = path.read_bytes()
    inventory = {n: {"sha256": policy.sha(d), "bytes": len(d)} for n, d in files.items()}
    files["bundle-inventory.json"] = policy.canonical(inventory)
    check_onedir({f"{APP}/{n}": d for n, d in files.items()}, args.version, args.source_sha)
    with (folder / "bundle-inventory.json").open("xb") as stream:
        stream.write(files["bundle-inventory.json"])
    print("PUBLIC_CANDIDATE_FOLDER_VERIFIED")


if __name__ == "__main__":
    main()
