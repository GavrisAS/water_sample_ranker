"""Create a reviewed projection without inheriting any local Git objects."""

import argparse
from pathlib import Path

from scripts.public_gate import PRIVATE_DOCUMENTS, allowed_names, check_source, public_document
from water_sample_ranker.handoff.builder import projection


def prepare(root: Path, target: Path) -> None:
    names = allowed_names(root)
    target.mkdir(parents=True, exist_ok=False)
    for name in sorted(names):
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("PUBLIC_SOURCE_MISSING_OR_LINK")
        if name.startswith("docs/") and source.name in PRIVATE_DOCUMENTS:
            data = public_document(source.name)
        else:
            data = source.read_bytes()
            if source.suffix == ".py":
                data, _ = projection(data)
        check_source(name, data, names)
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    prepare(Path.cwd(), args.output_dir)
    print("PUBLIC_PROJECTION_CREATED — run all gates before publication")


if __name__ == "__main__":
    main()
