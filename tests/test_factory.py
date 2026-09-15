import pandas as pd

from belgium_public.config import SourceSpec
from belgium_public.planning import select_sources, year_windows


def _s(source_id: str, tier: str, mode: str = "historical") -> SourceSpec:
    return SourceSpec(id=source_id, provider="Elia", name=source_id, family="x", endpoint="x", documentation="x", coverage="x", granularity="PT15M", timezone="Europe/Brussels", primary_key=["datetime"], publication_timing="x", pit_status="x", tier=tier, mode=mode)


def test_year_windows_are_bounded_and_cover_range():
    first = pd.Timestamp("2024-05-22T00:00:00Z")
    last = pd.Timestamp("2026-09-15T10:00:00Z")
    windows = list(year_windows(first, last))
    assert len(windows) == 3
    assert windows[0][0] <= first < windows[0][1]
    assert windows[-1][0] <= last < windows[-1][1]
    assert all(b > a for a, b in windows)
    assert all(windows[i][1] == windows[i + 1][0] for i in range(len(windows) - 1))


def test_default_historical_selection_excludes_extended():
    sources = [_s("core", "core"), _s("pre", "core_pre_mari"), _s("ext", "extended")]
    selected = select_sources(sources, mode="bootstrap", explicit=set(), tiers=set())
    assert {s.id for s in selected} == {"core", "pre"}
    ext = select_sources(sources, mode="bootstrap", explicit=set(), tiers={"extended"})
    assert {s.id for s in ext} == {"ext"}


def test_lab_optional_is_never_selected_implicitly():
    sources = [_s("lab", "lab_optional"), _s("core", "core")]
    selected = select_sources(sources, mode="bootstrap", explicit=set(), tiers=set())
    assert {s.id for s in selected} == {"core"}
    explicit = select_sources(sources, mode="bootstrap", explicit={"lab"}, tiers=set())
    assert {s.id for s in explicit} == {"lab"}
