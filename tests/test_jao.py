from belgium_public.jao import JAOCollectorError, _rows_from_payload, _validate_final_computation_rows


def test_rows_from_payload_accepts_list_and_common_wrappers():
    rows = [{"dateTimeUtc": "2026-01-01T00:00:00Z", "ram": 100, "ptdf_BE": 0.1, "cnec": "x"}]
    assert _rows_from_payload(rows) == rows
    assert _rows_from_payload({"data": rows}) == rows
    assert _rows_from_payload({"results": rows}) == rows


def test_final_computation_schema_requires_ram_ptdf_and_cnec():
    good = [{"dateTimeUtc": "2026-01-01T00:00:00Z", "ram": 100, "ptdf_BE": 0.1, "cnec": "x"}]
    _validate_final_computation_rows(good)

    bad = [{"dateTimeUtc": "2026-01-01T00:00:00Z", "ram": 100, "cnec": "x"}]
    try:
        _validate_final_computation_rows(bad)
    except JAOCollectorError:
        pass
    else:
        raise AssertionError("missing PTDF must fail closed")
