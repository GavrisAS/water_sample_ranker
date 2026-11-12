"""Portable configuration, discovery by workbook roles, atomic writes."""

import math
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from water_sample_ranker.readers.ooxml import Book

TEMPLATE = Path("templates/Water_Sample_Ranking_Calculator.xlsx")
ROLES = ("fund", "lha", "well_history", "gdi", "daily", "monitoring", "tech_mode")
OPTIONAL_ROLES = ("gtm_plan", "geodata")


def project_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


@dataclass(frozen=True, slots=True)
class CriteriaConfig:
    cl_low: float = 2000.0
    cl_high: float = 8000.0
    cl_delta: float = 2000.0
    mineralization_max: float = 22000.0
    interval_days: float = 7.0
    water_l_day: float = 1000.0
    separator_m3_day: float = 6.912

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"INVALID_CRITERIA_PARAMETER: {key}; нужно конечное число >=0")
        if self.cl_high <= self.cl_low:
            raise ValueError("INVALID_CRITERIA_PARAMETER: cl_high должен быть больше cl_low")

    @property
    def policy_status(self) -> str:
        return (
            "CUSTOMER_STAGE_C_PROVISIONAL"
            if self == CriteriaConfig()
            else "CUSTOM_THRESHOLDS_PENDING_APPROVAL"
        )


@dataclass(slots=True)
class AppConfig:
    root: Path
    paths: dict[str, str] = field(default_factory=dict)
    selected_field: str = ""
    criteria: CriteriaConfig = field(default_factory=CriteriaConfig)
    extra: dict[str, Any] = field(default_factory=dict)

    def path(self, role: str) -> Path | None:
        text = self.paths.get(role, "")
        if not text:
            return None
        p = Path(text).expanduser()
        return p if p.is_absolute() else self.root / p


def discover(root: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    candidates: dict[str, list[Path]] = {r: [] for r in ROLES}
    for path in sorted((root / "data").glob("*")):
        if path.suffix.lower() not in {".xlsx", ".xlsm"} or path.name.startswith("~$"):
            continue
        try:
            with Book(path) as b:
                sheets = set(b.sheets)
                role = None
                if {"БД Сибнефтегаз", "Для слайда"} <= sheets:
                    role = "lha"
                elif {"БЛУ", "ПЛУ", "ХЛУ"} <= sheets:
                    role = "well_history"
                elif "ГДИС_Сибнефтегаз в целом" in sheets:
                    role = "gdi"
                elif "Добыча телеметрия" in sheets:
                    role = "daily"
                elif "Техрежим скважин" in sheets:
                    role = "monitoring"
                elif any(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", s.strip()) for s in sheets):
                    role = "fund"
                elif any("2026" in s and "Сентябрь" in s for s in sheets):
                    role = "tech_mode"
                if role:
                    candidates[role].append(path)
        except OSError, ValueError, KeyError:
            continue
    # Prefer autonomous variant only when it is the unique no-external-links candidate.
    monitors = candidates["monitoring"]
    if len(monitors) > 1:
        autonomous = []
        for p in monitors:
            with Book(p) as b:
                if not any(n.startswith("xl/externalLinks/") for n in b.zip.namelist()):
                    autonomous.append(p)
        if len(autonomous) == 1:
            candidates["monitoring"] = autonomous
    paths = {r: ps[0].relative_to(root).as_posix() for r, ps in candidates.items() if len(ps) == 1}
    paths["calculator"] = TEMPLATE.as_posix()
    return paths, {
        r: [p.relative_to(root).as_posix() for p in ps]
        for r, ps in candidates.items()
        if len(ps) > 1
    }


def load_config(root: Path | None = None, config: Path | None = None) -> AppConfig:
    root = (root or project_root()).resolve()
    file = config or root / "config.toml"
    if not file.is_absolute():
        file = root / file
    defaults, ambiguous = discover(root)
    raw = tomllib.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    top_n = raw.get("top_n", 5)
    if type(top_n) is not int or not 1 <= top_n <= 30:
        raise ValueError("INVALID_TOP_N: нужно целое число 1…30")
    from dataclasses import fields

    params = raw.get("criteria", {})
    known = {f.name for f in fields(CriteriaConfig)}
    if not isinstance(params, dict) or set(params) - known:
        raise ValueError("INVALID_CRITERIA_SCHEMA: неизвестные параметры")
    user_paths = raw.get("paths", {})
    if not isinstance(user_paths, dict) or any(not isinstance(v, str) for v in user_paths.values()):
        raise ValueError("INVALID_PATHS_SCHEMA: пути должны быть строками")
    criteria = CriteriaConfig(**params)
    paths = defaults | raw.get("paths", {})
    extra = {k: v for k, v in raw.items() if k not in {"paths", "criteria", "selected_field"}}
    extra["discovery_ambiguities"] = {
        r: v for r, v in ambiguous.items() if r not in raw.get("paths", {})
    }
    return AppConfig(root, paths, raw.get("selected_field", ""), criteria, extra)


def save_config(config: AppConfig, path: Path | None = None) -> None:
    from dataclasses import asdict

    destination = path or config.root / "config.toml"
    paths = {}
    for role, text in config.paths.items():
        p = Path(text)
        try:
            paths[role] = str(p.relative_to(config.root)) if p.is_absolute() else text
        except ValueError:
            paths[role] = text
    payload = config.extra | {
        "paths": paths,
        "selected_field": config.selected_field,
        "criteria": asdict(config.criteria),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destination.parent, suffix=".toml")
    try:
        with os.fdopen(fd, "wb") as stream:
            tomli_w.dump(payload, stream)
        os.replace(tmp, destination)
    finally:
        Path(tmp).unlink(missing_ok=True)
