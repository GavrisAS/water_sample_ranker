"""GUI-independent progress reporting for the application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    """One deterministic service-pipeline progress checkpoint."""

    percent: int
    stage: str

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= 100:
            raise ValueError(f"Progress percent must be between 0 and 100: {self.percent}")


ProgressCallback = Callable[[ProgressUpdate], None]
