"""Физическая допустимость отделена от разбора числа и бизнес-порогов."""

from math import isfinite


def valid_measurement(value: float | None) -> bool:
    """Cl, минерализация и Qв неотрицательны; верхний предел не задан."""
    return value is not None and isfinite(value) and value >= 0
