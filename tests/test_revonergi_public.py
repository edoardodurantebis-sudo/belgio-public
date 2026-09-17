from datetime import date

import pandas as pd
import pytest
from unittest.mock import patch

from belgium_public.revonergi_public import parse_revonergi_payload, validate_revonergi_day

def test_dead_endpoint_circuit_does_not_repeat_hundreds_of_failed_days(tmp_path):
    from belgium_public.revonergi_public import backfill_revonergi_history,RevonergiPublicError
    with patch('belgium_public.revonergi_public.fetch_revonergi_day',side_effect=RevonergiPublicError('unavailable')) as fetch:
        rows,meta=backfill_revonergi_history(pd.DataFrame(),date(2025,10,1),date(2026,9,16),tmp_path,max_workers=2)
    assert rows.empty
    assert fetch.call_count==4
    assert meta['circuit_open'] is True
    assert meta['deferred_days']>300

def test_current_points_schema_has_explicit_kwh_units_even_at_extreme_prices():
    day=date(2026,9,16)
    points=[{'t':f'{h:02d}:{m:02d}','price':-6.0,'idx':h*4+m//15} for h in range(24) for m in (0,15,30,45)]
    frame=parse_revonergi_payload({'ok':True,'date':str(day),'points':points},day)
    assert validate_revonergi_day(day,frame)['rows']==96
    assert frame.entry_price.eq(-6000).all()
    with pytest.raises(RuntimeError):
        parse_revonergi_payload({'ok':True,'date':'2026-09-15','points':points},day)

def test_local_labels_cannot_guess_autumn_fold_or_accept_wrong_day_cardinality():
    day=date(2025,10,26)
    points=[{'t':f'{h:02d}:{m:02d}','price':.1} for h in range(24) for m in (0,15,30,45)]
    frame=parse_revonergi_payload({'ok':True,'date':str(day),'points':points},day)
    with pytest.raises(RuntimeError):validate_revonergi_day(day,frame)
    normal=pd.date_range('2026-09-16T00:00:00',periods=92,freq='15min',tz='Europe/Brussels').tz_convert('UTC')
    with pytest.raises(RuntimeError):validate_revonergi_day(date(2026,9,16),pd.DataFrame({'delivery_start_utc':normal,'entry_price':1.}))


def test_parse_revonergi_hhmm_eur_mwh_rows():
    day = date(2025, 10, 6)
    payload = {
        "prijzen": [
            {"tijd": f"{h:02d}:{m:02d}", "price_eur_mwh": 50 + h + m / 60}
            for h in range(24)
            for m in (0, 15, 30, 45)
        ]
    }
    x = parse_revonergi_payload(payload, day)
    assert len(x) == 96
    assert x["entry_price"].iloc[0] == 50.0
    meta = validate_revonergi_day(day, x)
    assert meta["rows"] == 96
    assert meta["common_step_seconds"] == 900.0


def test_parse_revonergi_generic_eur_kwh_converts_to_mwh():
    day = date(2026, 1, 1)
    payload = {
        "data": [
            {"time": f"{h:02d}:{m:02d}", "price": 0.08 + (h * 4 + m // 15) / 10000}
            for h in range(24)
            for m in (0, 15, 30, 45)
        ]
    }
    x = parse_revonergi_payload(payload, day)
    assert len(x) == 96
    assert 79 < x["entry_price"].iloc[0] < 81
    validate_revonergi_day(day, x)


def test_parse_revonergi_time_to_price_map():
    day = date(2026, 2, 2)
    payload = {
        "prices": {
            f"{h:02d}:{m:02d}": 100 + h + m / 100
            for h in range(24)
            for m in (0, 15, 30, 45)
        }
    }
    x = parse_revonergi_payload(payload, day)
    assert len(x) == 96
    validate_revonergi_day(day, x)


def test_parse_revonergi_numeric_array_uses_local_day_dst():
    # 2026-03-29 Brussels spring DST day = 92 quarters.
    day = date(2026, 3, 29)
    x = parse_revonergi_payload({"prices": [100.0] * 92}, day)
    assert len(x) == 92
    assert validate_revonergi_day(day, x)["rows"] == 92


def test_pre_qh_numeric_day_accepts_24_hourly_values():
    day = date(2025, 9, 1)
    x = parse_revonergi_payload({"prices": [70.0] * 24}, day)
    assert len(x) == 24
    meta = validate_revonergi_day(day, x)
    assert meta["common_step_seconds"] == 3600.0
