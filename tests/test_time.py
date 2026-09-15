from datetime import date
import pandas as pd
from belgium_public.canonical import resolve_col
from belgium_public.market_tape import belgian_delivery_day_utc


def test_dst_physical_time_identity_uses_utc():
    local = pd.to_datetime(["2026-10-25 02:15:00+02:00", "2026-10-25 02:15:00+01:00"], utc=True)
    assert local[0] != local[1]
    assert local.is_unique


def test_column_resolution_is_schema_tolerant_not_semantic_guessing():
    df = pd.DataFrame(columns=["Date Time", "Resolution code"])
    assert resolve_col(df, "datetime") == "Date Time"
    assert resolve_col(df, "price") is None


def test_jao_business_day_dst_conversion():
    from belgium_public.jao import business_day_param
    assert business_day_param(date(2026, 1, 15)) == "2026-01-14T23:00:00.000Z"
    assert business_day_param(date(2026, 7, 15)) == "2026-07-14T22:00:00.000Z"


def test_belgian_delivery_day_dst_lengths():
    spring_a, spring_b = belgian_delivery_day_utc(date(2026, 3, 29))
    autumn_a, autumn_b = belgian_delivery_day_utc(date(2026, 10, 25))
    assert (spring_b - spring_a).total_seconds() == 23 * 3600
    assert (autumn_b - autumn_a).total_seconds() == 25 * 3600
