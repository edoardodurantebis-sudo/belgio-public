from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .profit_discovery import Atom, _bh, discover, mask_atoms, summarize_pnl


def _robust(stats: dict, *, require_fdr: bool = False) -> bool:
    ok = (
        stats.get("verdict") == "ECON_POSITIVE_ROBUSTNESS_SCREEN"
        and int(stats.get("n_days", 0)) >= 10
        and int(stats.get("n_mtu", 0)) >= 30
        and float(stats.get("positive_month_fraction", 0.0)) >= 0.60
        and float(stats.get("no_best5_total_pnl_1mw_eur", -1.0)) > 0.0
    )
    if require_fdr:
        ok = ok and bool(stats.get("fdr_pass", False))
    return bool(ok)


def _evaluate_slice(df: pd.DataFrame, candidates: list[dict], mask: pd.Series, prefix: str) -> list[dict]:
    rows: list[dict] = []
    for c in candidates:
        atoms = [Atom(**a) for a in json.loads(c["definition_json"])]
        m = mask_atoms(df, atoms) & mask
        st = summarize_pnl(df.loc[m])
        row = {
            "definition_json": c["definition_json"],
            "n_factors": c["n_factors"],
            "train_score": c["train_score"],
            **{f"{prefix}_{k}": v for k, v in st.items()},
            "daily_sign_pvalue": st.get("daily_sign_pvalue"),
        }
        rows.append(row)
    _bh(rows, alpha=0.10)
    for row in rows:
        row[f"{prefix}_qvalue"] = row.pop("qvalue", None)
        row[f"{prefix}_fdr_pass"] = row.pop("fdr_pass", False)
        row.pop("daily_sign_pvalue", None)
    return rows


def run_two_stage_profit_lab(
    panel_path: Path,
    feature_registry_path: Path,
    out_path: Path,
    validation_from: str,
    final_holdout_from: str,
) -> dict:
    """Train -> validation/FDR -> untouched final holdout economic gate.

    Thresholds and combinations are discovered only before ``validation_from``.
    The validation slice is then used for multiplicity-controlled candidate
    selection. Only candidates that pass that frozen validation gate are opened
    on the final slice. No threshold or definition is changed after train.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not panel_path.exists():
        payload = {"status": "BLOCKED_MISSING_PROFIT_PANEL", "candidates": []}
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    if not feature_registry_path.exists():
        payload = {"status": "BLOCKED_MISSING_FEATURE_REGISTRY", "candidates": []}
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    panel = pd.read_parquet(panel_path)
    required = {"delivery_start_utc", "entry_price", "imbalance_price", "system_view", "gross_pnl_1mw_eur"}
    missing = sorted(required - set(panel.columns))
    if missing:
        payload = {"status": "BLOCKED_BAD_PROFIT_PANEL", "missing": missing, "candidates": []}
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    validation_ts = pd.Timestamp(validation_from, tz="UTC")
    final_ts = pd.Timestamp(final_holdout_from, tz="UTC")
    if final_ts <= validation_ts:
        raise ValueError("final_holdout_from must be after validation_from")

    ts = pd.to_datetime(panel["delivery_start_utc"], utc=True, errors="coerce")
    train_mask = ts < validation_ts
    validation_mask = (ts >= validation_ts) & (ts < final_ts)
    final_mask = ts >= final_ts

    reg = pd.read_csv(feature_registry_path)
    if "pit_status" in reg:
        reg = reg[reg["pit_status"].astype(str).str.upper().eq("CERTIFIED")].copy()

    train = panel.loc[train_mask].copy()
    if len(train) < 120 or int(validation_mask.sum()) < 30 or int(final_mask.sum()) < 30:
        payload = {
            "status": "BLOCKED_INSUFFICIENT_TEMPORAL_SPLITS",
            "train_rows": int(train_mask.sum()),
            "validation_rows": int(validation_mask.sum()),
            "final_rows": int(final_mask.sum()),
            "candidates": [],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    discovered = discover(train, reg)
    validation_rows = _evaluate_slice(panel, discovered, validation_mask, "val")

    selected: list[dict] = []
    validation_by_def: dict[str, dict] = {}
    for row in validation_rows:
        val_stats = {
            k.removeprefix("val_"): v
            for k, v in row.items()
            if k.startswith("val_") and k not in {"val_qvalue", "val_fdr_pass"}
        }
        val_stats["fdr_pass"] = bool(row.get("val_fdr_pass", False))
        row["validation_gate_pass"] = _robust(val_stats, require_fdr=True)
        validation_by_def[row["definition_json"]] = row
        if row["validation_gate_pass"]:
            selected.append(row)

    final_base = [
        {
            "definition_json": r["definition_json"],
            "n_factors": r["n_factors"],
            "train_score": r["train_score"],
        }
        for r in selected
    ]
    final_rows = _evaluate_slice(panel, final_base, final_mask, "final") if final_base else []
    final_by_def = {r["definition_json"]: r for r in final_rows}

    combined: list[dict] = []
    for row in validation_rows:
        out = dict(row)
        fin = final_by_def.get(row["definition_json"])
        if fin:
            for k, v in fin.items():
                if k.startswith("final_"):
                    out[k] = v
            final_stats = {
                k.removeprefix("final_"): v
                for k, v in fin.items()
                if k.startswith("final_") and k not in {"final_qvalue", "final_fdr_pass"}
            }
            final_stats["fdr_pass"] = bool(fin.get("final_fdr_pass", False))
            out["final_gate_pass"] = _robust(final_stats, require_fdr=True)
        else:
            out["final_gate_pass"] = False

        if out.get("validation_gate_pass") and out.get("final_gate_pass"):
            out["machine_status"] = "REVIEW_READY"
        elif out.get("validation_gate_pass"):
            out["machine_status"] = "FINAL_HOLDOUT_FAIL"
        else:
            out["machine_status"] = "VALIDATION_FAIL"
        combined.append(out)

    def score(row: dict) -> tuple:
        status_order = {"REVIEW_READY": 0, "FINAL_HOLDOUT_FAIL": 1, "VALIDATION_FAIL": 2}
        try:
            final_pnl = float(row.get("final_total_pnl_1mw_eur", -1e18))
            if not np.isfinite(final_pnl):
                final_pnl = -1e18
        except Exception:
            final_pnl = -1e18
        try:
            val_pnl = float(row.get("val_total_pnl_1mw_eur", -1e18))
            if not np.isfinite(val_pnl):
                val_pnl = -1e18
        except Exception:
            val_pnl = -1e18
        return (status_order.get(str(row.get("machine_status")), 9), -final_pnl, -val_pnl)

    combined.sort(key=score)
    views = sorted(set(panel["system_view"].dropna().astype(str)))
    payload = {
        "status": "PASS" if combined else "PASS_NO_CANDIDATES",
        "objective": "DIRECT_GROSS_PNL_ENTRY_TO_IMBALANCE_PT15_TWO_STAGE_OOS",
        "validation_from": validation_from,
        "final_holdout_from": final_holdout_from,
        "system_views": views,
        "train_rows": int(train_mask.sum()),
        "validation_rows": int(validation_mask.sum()),
        "final_rows": int(final_mask.sum()),
        "eligible_feature_count": int(len(reg)),
        "candidate_count": len(combined),
        "validation_gate_pass_count": int(sum(bool(r.get("validation_gate_pass")) for r in combined)),
        "review_ready_count": int(sum(r.get("machine_status") == "REVIEW_READY" for r in combined)),
        "candidates": combined[:200],
        "policy": (
            "Definitions and thresholds frozen on train. Validation slice performs FDR-controlled selection. "
            "Only validation-pass candidates are evaluated on the final holdout; both stages must pass robustness/FDR."
        ),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
