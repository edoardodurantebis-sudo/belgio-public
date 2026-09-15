from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

from belgium_public.config import SourceSpec

CORE_TIERS = {"core", "core_pre_mari", "jao_canary"}


def year_windows(first: pd.Timestamp, last: pd.Timestamp) -> Iterator[tuple[pd.Timestamp, pd.Timestamp]]:
    """Yield bounded, non-overlapping yearly UTC windows covering [first, last]."""
    start = pd.Timestamp(first).floor("D")
    stop = pd.Timestamp(last).ceil("D") + pd.Timedelta(days=1)
    cursor = start
    while cursor < stop:
        nxt = min(cursor + pd.DateOffset(years=1), stop)
        nxt = pd.Timestamp(nxt)
        yield cursor, nxt
        cursor = nxt


def select_sources(
    sources: list[SourceSpec],
    *,
    mode: str,
    explicit: set[str],
    tiers: set[str],
) -> list[SourceSpec]:
    """Resolve source selection without silently pulling extended/lab datasets."""
    selected = [s for s in sources if not explicit or s.id in explicit]

    if mode == "nrt":
        selected = [s for s in selected if s.mode == "snapshot"]
    elif mode in {"bootstrap", "incremental"}:
        selected = [s for s in selected if s.mode in {"historical", "jao_daily"}]
    elif mode == "all":
        selected = [s for s in selected if s.mode in {"historical", "jao_daily", "snapshot"}]
    else:
        raise ValueError(f"unsupported mode: {mode}")

    if tiers:
        selected = [s for s in selected if s.tier in tiers]
    elif not explicit and mode != "nrt":
        selected = [s for s in selected if s.tier in CORE_TIERS]

    if not explicit:
        selected = [s for s in selected if s.tier != "lab_optional"]

    return selected
