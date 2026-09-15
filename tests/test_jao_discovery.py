from belgium_public.jao_discovery import _schema_signature


def test_domain_schema_requires_ram_and_ptdf():
    payload = {"rows": [{"cnecName": "x", "ram": 100.0, "ptdf_BE": 0.21, "ptdf_DE": -0.1}]}
    sig = _schema_signature(payload)
    assert sig["has_ram"]
    assert sig["has_ptdf"]
    assert sig["has_cnec_identity"]


def test_non_domain_payload_does_not_verify():
    sig = _schema_signature({"maxExchanges": [{"dateTimeUtc": "x", "BE_FR": 10}]})
    assert not (sig["has_ram"] and sig["has_ptdf"])
