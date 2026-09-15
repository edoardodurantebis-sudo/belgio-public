from __future__ import annotations
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .jao import business_day_param


def _sample_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload[:5] if isinstance(x, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[:5]
    return []


def _schema_signature(payload: Any) -> dict:
    rows = _sample_rows(payload)
    keys = sorted({str(k) for r in rows for k in r})
    norm = {k.lower().replace("_", "").replace("-", "") for k in keys}
    return {
        "sample_rows": len(rows),
        "keys": keys[:200],
        "has_ram": "ram" in norm or any(k.endswith("ram") for k in norm),
        "has_ptdf": any(k.startswith("ptdf") or "ptdf" in k for k in norm),
        "has_cnec_identity": any("cnec" in k or "criticalnetworkelement" in k for k in norm),
    }


def audit_core_domain(day: date, out_path: Path, timeout: int = 45) -> dict:
    start = business_day_param(day)
    next_day = business_day_param(day + timedelta(days=1))
    candidates = [
        {
            "name": "legacy_core_final_computation",
            "url": "https://publicationtool.jao.eu/core/api/core/finalComputation/index",
            "params": {"date": start, "skip": 0, "take": 5},
            "basis": "legacy Core API URL family documented by JAO for maxExchanges",
        },
        {
            "name": "modern_data_final_computation",
            "url": "https://publicationtool.jao.eu/core/api/data/finalComputation",
            "params": {"FromUtc": start, "ToUtc": next_day, "skip": 0, "take": 5},
            "basis": "current JAO publication-tool API family used by Nordic; probe only, never assumed valid for Core",
        },
    ]
    results = []
    for candidate in candidates:
        item = {k: v for k, v in candidate.items() if k != "params"}
        item["request_params"] = candidate["params"]
        try:
            r = requests.get(candidate["url"], params=candidate["params"], timeout=timeout, headers={"User-Agent": "belgio-public/0.2"})
            item["http_status"] = r.status_code
            item["response_url"] = r.url
            item["content_type"] = r.headers.get("content-type")
            if r.status_code == 200:
                try:
                    payload = r.json()
                    sig = _schema_signature(payload)
                    item["schema"] = sig
                    item["verified_domain_endpoint"] = bool(sig["has_ram"] and sig["has_ptdf"])
                except Exception as e:
                    item["verified_domain_endpoint"] = False
                    item["error"] = f"json_parse:{type(e).__name__}"
            else:
                item["verified_domain_endpoint"] = False
                item["body_prefix"] = r.text[:300]
        except Exception as e:
            item["verified_domain_endpoint"] = False
            item["error"] = repr(e)
        results.append(item)

    verified = [x for x in results if x.get("verified_domain_endpoint")]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "business_day": day.isoformat(),
        "status": "VERIFIED" if verified else "BLOCKED_UNVERIFIED",
        "verified": verified,
        "probes": results,
        "policy": "A Core CNEC/RAM/PTDF endpoint is promoted only after live HTTP 200 plus RAM and PTDF schema evidence. URL-pattern guesses are not data sources.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
