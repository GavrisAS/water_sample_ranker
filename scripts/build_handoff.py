"""Собрать локальный share-safe handoff независимо от текущего каталога."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from water_sample_ranker.handoff.builder import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
