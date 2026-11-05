"""Привязка свежей сборки к содержимому кода/ресурсов; не цифровая подпись."""

import hashlib
import json
from pathlib import Path


def build_inputs(root: Path) -> dict[str, str]:
    names = {
        p.relative_to(root).as_posix() for p in (root / "src/water_sample_ranker").rglob("*.py")
    }
    names.update(
        {
            "pyproject.toml",
            "uv.lock",
            "config.example.toml",
            "scripts/build_frozen.py",
            "scripts/build_windows.ps1",
            "templates/Water_Sample_Ranking_Calculator.xlsx",
            "docs/README_ПЕРВЫЙ_ЗАПУСК.txt",
        }
    )
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in sorted(names)}


def content_digest(inputs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
