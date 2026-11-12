"""Строгие opt-in JSON v1 порты. Это предложенный обменный контракт, не формат заказчика."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from water_sample_ranker.domain.dates import parse_partial_date
from water_sample_ranker.domain.models import PartialDate, SourceLocation


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    field: str
    well: str
    stage: int
    plast: str
    date: PartialDate
    label: str
    location: SourceLocation
    raw_date: str | None = None


@dataclass(frozen=True, slots=True)
class GeoPoint:
    field: str
    well: str
    stage: int
    plast: str
    x: float
    y: float
    crs: str
    coordinate_source: str
    version: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class OptionalInputs:
    status: str = "SOURCE_UNAVAILABLE"
    plans: tuple[PlannedEvent, ...] = ()
    points: tuple[GeoPoint, ...] = ()
    message: str = "Файл отсутствует — критерий не подтверждён"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_optional(path: Path | None, role: str) -> OptionalInputs:
    if path is None:
        return OptionalInputs()
    if not path.is_file():
        return OptionalInputs(message="SOURCE_UNAVAILABLE: настроенный файл не найден")
    try:
        if path.suffix.lower() != ".json" or path.stat().st_size > 4 * 1024**2:
            raise ValueError("FORMAT")
        if role not in {"gtm-plan", "geodata"}:
            raise ValueError("ROLE")
        # stat alone cannot enforce the limit if a producer replaces/grows the file.
        with path.open("rb") as stream:
            payload = stream.read(4 * 1024**2 + 1)
        if len(payload) > 4 * 1024**2:
            raise ValueError("FORMAT")
        raw: Any = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(raw, dict) or set(raw) != {"schema", "records"}:
            raise ValueError("ROOT")
        if raw["schema"] != f"water-{role}-v1" or not isinstance(raw["records"], list):
            raise ValueError("SCHEMA")
        common = {"field", "well", "stage", "plast"}
        extra = (
            {"date", "precision", "label"}
            if role == "gtm-plan"
            else {"x", "y", "crs", "coordinate_source", "version"}
        )
        plans, points, keys = [], [], set()
        for index, r in enumerate(raw["records"]):
            if not isinstance(r, dict) or set(r) != common | extra:
                raise ValueError("COLUMNS")
            if any(
                not isinstance(r[k], str) or not r[k].strip()
                for k in (common | extra) - {"stage", "x", "y"}
            ):
                raise ValueError("TEXT")
            if type(r["stage"]) is not int or r["stage"] < 0:
                raise ValueError("STAGE")
            # No guessed alias/plast conversion: caller must supply canonical identity.
            key = (r["field"], r["well"], r["stage"], r["plast"])
            loc = SourceLocation(str(path), None, f"/records/{index}")
            if role == "gtm-plan":
                d = parse_partial_date(r["date"])
                if not d or d.precision.value != r["precision"]:
                    raise ValueError("DATE_PRECISION")
                plans.append(PlannedEvent(*key, d, r["label"], loc, r["date"]))
            else:
                if key in keys:
                    raise ValueError("DUPLICATE_IDENTITY")
                keys.add(key)
                if any(
                    type(r[k]) not in {int, float} or not math.isfinite(r[k]) for k in ("x", "y")
                ):
                    raise ValueError("COORDINATES")
                # CRS interpretation is deliberately limited and explicit, never guessed.
                if r["crs"] != "EPSG:4326" or not (-180 <= r["x"] <= 180 and -90 <= r["y"] <= 90):
                    raise ValueError("CRS_OR_RANGE")
                points.append(
                    GeoPoint(
                        *key, r["x"], r["y"], r["crs"], r["coordinate_source"], r["version"], loc
                    )
                )
        return OptionalInputs(
            "AVAILABLE_RULE_PENDING",
            tuple(plans),
            tuple(points),
            "Контракт прочитан; схема обмена и применимость не утверждены",
        )
    except ValueError, OSError, TypeError, OverflowError, RecursionError:
        # Integers beyond float range are invalid coordinates, not reader crashes.
        return OptionalInputs(
            "SCHEMA_DRIFT",
            message="SCHEMA_DRIFT: ожидается строгий JSON v1; источник целиком отклонён",
        )
