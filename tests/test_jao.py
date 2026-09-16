from datetime import date

import requests

import belgium_public.jao as jao
from belgium_public.jao import (
    JAOCollectorError,
    _range_windows,
    _request_with_retry,
    _rows_from_payload,
    _validate_final_computation_rows,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


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


def test_range_windows_are_one_business_day_each():
    windows = list(_range_windows(date(2026, 9, 10), date(2026, 9, 13)))
    assert windows == [
        (date(2026, 9, 10), date(2026, 9, 11)),
        (date(2026, 9, 11), date(2026, 9, 12)),
        (date(2026, 9, 12), date(2026, 9, 13)),
    ]


def test_request_retries_transport_then_succeeds(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("temporary refusal")
        return FakeResponse(200)

    monkeypatch.setattr(jao.sleep_time, "sleep", lambda _: None)
    monkeypatch.setattr(jao.random, "uniform", lambda a, b: 0.0)
    response, attempts = _request_with_retry(
        "https://example.test",
        params={},
        timeout=1,
        retries=4,
        request_get=fake_get,
    )
    assert response.status_code == 200
    assert attempts == 3
    assert len(calls) == 3


def test_request_retries_503_then_succeeds(monkeypatch):
    responses = [FakeResponse(503, "temporary"), FakeResponse(200, "ok")]

    def fake_get(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(jao.sleep_time, "sleep", lambda _: None)
    monkeypatch.setattr(jao.random, "uniform", lambda a, b: 0.0)
    response, attempts = _request_with_retry(
        "https://example.test",
        params={},
        timeout=1,
        retries=4,
        request_get=fake_get,
    )
    assert response.status_code == 200
    assert attempts == 2


def test_request_does_not_retry_hard_400(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse(400, "bad range")

    monkeypatch.setattr(jao.sleep_time, "sleep", lambda _: None)
    response, attempts = _request_with_retry(
        "https://example.test",
        params={},
        timeout=1,
        retries=4,
        request_get=fake_get,
    )
    assert response.status_code == 400
    assert attempts == 1
    assert len(calls) == 1
