"""UI formatting helpers for file sizes and dates."""

from __future__ import annotations

import datetime


def fmt_size(n: int | None) -> str:
    """Format byte count into human-readable string: None -> '…', else 1024-based with 1 decimal above bytes."""
    if n is None:
        return "…"
    if n < 1024:
        return f"{n} B"

    units = ["KB", "MB", "GB", "TB"]
    val = n / 1024.0
    unit_idx = 0

    while unit_idx < len(units) - 1 and round(val, 1) >= 1024.0:
        val /= 1024.0
        unit_idx += 1

    return f"{val:.1f} {units[unit_idx]}"


def fmt_date(ts: float | None) -> str:
    """Format epoch timestamp as 'YYYY-MM-DD', or empty string if None or non-positive."""
    if ts is None or ts <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return ""
