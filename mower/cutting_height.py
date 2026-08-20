from __future__ import annotations


MINIMUM_MM = 20
MAXIMUM_MM = 60
RECOMMENDED_MINIMUM_MM = 30
LOW_HEIGHT_WARNING_BELOW_MM = 25


def supports_metric_cutting_height(model: str | None) -> bool:
    """Only convert percent to millimetres for the verified mower family."""

    normalized = " ".join(str(model or "").upper().split())
    return "580 EPOS" in normalized


def cutting_height_percent_to_mm(value: int) -> int:
    percent = int(value)
    if not 0 <= percent <= 100:
        raise ValueError("Die Husqvarna-Schnitthöhe muss zwischen 0 und 100 Prozent liegen.")
    return round(MINIMUM_MM + (MAXIMUM_MM - MINIMUM_MM) * percent / 100)


def cutting_height_mm_to_percent(value: int) -> int:
    millimetres = int(value)
    if not MINIMUM_MM <= millimetres <= MAXIMUM_MM:
        raise ValueError(
            f"Die Schnitthöhe muss zwischen {MINIMUM_MM} und {MAXIMUM_MM} mm liegen."
        )
    # Explicit half-up rounding avoids Python's banker rounding at 12.5%.
    scaled = (millimetres - MINIMUM_MM) * 100 / (MAXIMUM_MM - MINIMUM_MM)
    return int(scaled + 0.5)
