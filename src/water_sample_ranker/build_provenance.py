"""Привязка свежей сборки к содержимому кода/ресурсов; не цифровая подпись."""

import hashlib
import json
from pathlib import Path


def build_inputs(root: Path) -> dict[str, str]:
    approved = (root / "scripts/public_files.txt").read_text(encoding="utf-8").splitlines()
    names = {n for n in approved if n.startswith("src/water_sample_ranker/") and n.endswith(".py")}
    names.update(n for n in approved if n.startswith("docs/licenses/"))
    names.update(
        {
            "pyproject.toml",
            "uv.lock",
            "config.example.toml",
            "scripts/build_frozen.py",
            "scripts/build_windows.ps1",
            "templates/Water_Sample_Ranking_Calculator.xlsx",
            "docs/README_ПЕРВЫЙ_ЗАПУСК.txt",
            "docs/THIRD_PARTY_NOTICES.txt",
            "docs/THIRD_PARTY_LICENSES.json",
            "scripts/public_files.txt",
        }
    )
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in sorted(names)}


def content_digest(inputs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
