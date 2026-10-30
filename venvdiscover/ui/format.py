"""UI formatting helpers for file sizes and dates."""

from __future__ import annotations

import datetime

from venvdiscover.model import SHARED_NOTE_MIN


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


def shares_notably(size: int | None, size_unique: int | None) -> bool:
    """True when at least SHARED_NOTE_MIN of a folder stays on disk after deleting it (hard-linked elsewhere)."""
    return size is not None and size_unique is not None and size - size_unique >= SHARED_NOTE_MIN


def fmt_frees(size: int | None, size_unique: int | None) -> str:
    """The Size column: what deleting frees, with ' *' when a notable part is shared with other folders."""
    text = fmt_size(size_unique if size_unique is not None else size)
    return f"{text} *" if shares_notably(size, size_unique) else text


def fmt_shared_note(size: int | None, size_unique: int | None) -> str:
    """'* 3.0 GB on disk, 2.1 GB shared with other folders: deleting frees 0.9 GB', or '' if nothing notable."""
    if not shares_notably(size, size_unique):
        return ""
    assert size is not None and size_unique is not None
    return (f"* {fmt_size(size)} on disk, {fmt_size(size - size_unique)} shared with other folders "
            f"(like a package cache): deleting frees {fmt_size(size_unique)}")


def fmt_date(ts: float | None) -> str:
    """Format epoch timestamp as 'YYYY-MM-DD', or empty string if None or non-positive."""
    if ts is None or ts <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return ""
