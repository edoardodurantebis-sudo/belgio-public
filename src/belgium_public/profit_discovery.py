from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from itertools import combinations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Atom:
    feature: str
    op: str
    threshold: float
    label: str
    family: str


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def profit_factor(pnl: pd.Series) -> float:
    x = num(pnl).dropna()
    pos = float(x[x > 0].sum())
    neg = float(x[x < 0].sum())
    if neg == 0:
        return float("inf") if pos > 0 else float("nan")
    return pos / abs(neg)


def pnl_from_prices(entry: pd.Series, imbalance: pd.Series, system_view: str, mtu_hours: float = 0.25) -> pd.Series:
    """UK-compatible economics, adapted to Belgian PT15 settlement.

    SHORT_SYSTEM means the strategy benefits when imbalance is above entry.
    LONG_SYSTEM means the strategy benefits when imbalance is below entry.
    Returns gross EUR per 1 MW position per MTU; costs are intentionally separate.
    """
    e = num(entry)
    s = num(imbalance)
    view = str(system_view).upper()
    if view == "SHORT_SYSTEM":
        edge = s - e
    elif view == "LONG_SYSTEM":
        edge = e - s
    else:
        raise ValueError(f"BAD_SYSTEM_VIEW:{system_view}")
    return edge * float(mtu_hours)


def mask_atoms(df: pd.DataFrame, atoms: Iterable[Atom]) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for a in atoms:
        if a.feature not in df.columns:
            return pd.Series(False, index=df.index)
        x = num(df[a.feature])
        if a.op == "<=":
            m &= x <= a.threshold
        elif a.op == ">=":
            m &= x >= a.threshold
        else:
            raise ValueError(f"BAD_OP:{a.op}")
    return m.fillna(False)


def _binom_two_sided(values: np.ndarray) -> float | None:
    x = values[np.isfinite(values) & (values != 0)]
    n = len(x)
    if n < 8:
        return None
    pos = int((x > 0).sum())
    k = min(pos, n - pos)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return float(min(1.0, 2.0 * tail))


def summarize_pnl(rows: pd.DataFrame, pnl_col: str = "gross_pnl_1mw_eur") -> dict:
    if rows.empty or pnl_col not in rows:
        return {"n_mtu": 0, "n_days": 0, "verdict": "NO_EVALUABLE_ROWS"}
    r = rows.copy()
    r[pnl_col] = num(r[pnl_col])
    r = r[np.isfinite(r[pnl_col])].copy()
    if r.empty:
        return {"n_mtu": 0, "n_days": 0, "verdict": "NO_EVALUABLE_ROWS"}
    if "delivery_date" not in r:
        r["delivery_date"] = pd.to_datetime(r["delivery_start_utc"], utc=True, errors="coerce").dt.tz_convert("Europe/Brussels").dt.tz_localize(None).dt.normalize()
    daily = r.groupby("delivery_date")[pnl_col].sum().sort_index()
    monthly = daily.groupby(daily.index.to_period("M")).sum() if len(daily) else pd.Series(dtype=float)
    cum = daily.cumsum()
    drawdown = cum - cum.cummax()
    ordered = daily.sort_values(ascending=False)
    no5 = float(daily.drop(ordered.head(min(5, len(ordered))).index).sum()) if len(daily) else float("nan")
    no10 = float(daily.drop(ordered.head(min(10, len(ordered))).index).sum()) if len(daily) else float("nan")
    vals = daily.to_numpy(float)
    p = _binom_two_sided(vals)
    out = {
        "n_mtu": int(len(r)),
        "n_days": int(len(daily)),
        "mean_pnl_1mw_eur_per_mtu": float(r[pnl_col].mean()),
        "median_pnl_1mw_eur_per_mtu": float(r[pnl_col].median()),
        "total_pnl_1mw_eur": float(r[pnl_col].sum()),
        "mean_daily_pnl_1mw_eur": float(daily.mean()),
        "win_rate": float((r[pnl_col] > 0).mean()),
        "profit_factor": float(profit_factor(r[pnl_col])),
        "worst_day_pnl_1mw_eur": float(daily.min()),
        "max_drawdown_1mw_eur": float(-drawdown.min()) if len(drawdown) else float("nan"),
        "no_best5_total_pnl_1mw_eur": no5,
        "no_best10_total_pnl_1mw_eur": no10,
        "positive_month_fraction": float((monthly > 0).mean()) if len(monthly) else float("nan"),
        "daily_sign_pvalue": p,
    }
    if out["n_mtu"] < 30 or out["n_days"] < 10:
        verdict = "INSUFFICIENT_ECON_SAMPLE"
    elif out["mean_pnl_1mw_eur_per_mtu"] <= 0 or out["total_pnl_1mw_eur"] <= 0:
        verdict = "ECON_NEGATIVE"
    elif out["median_pnl_1mw_eur_per_mtu"] > 0 and out["profit_factor"] > 1.0 and no5 > 0:
        verdict = "ECON_POSITIVE_ROBUSTNESS_SCREEN"
    else:
        verdict = "ECON_POSITIVE_MIXED"
    out["verdict"] = verdict
    return out


def generate_atoms(train: pd.DataFrame, feature_registry: pd.DataFrame, quantiles=(0.05, 0.10, 0.20, 0.33, 0.67, 0.80, 0.90, 0.95)) -> list[Atom]:
    atoms: list[Atom] = []
    for _, row in feature_registry.iterrows():
        feature = str(row["column_name"])
        family = str(row.get("family", feature))
        if feature not in train:
            continue
        z = num(train[feature]).dropna()
        if len(z) < 120 or z.nunique() < 8:
            continue
        q = z.quantile(list(quantiles))
        seen: set[tuple[str, float]] = set()
        for qq in quantiles:
            op = "<=" if qq < 0.5 else ">="
            thr = float(q.loc[qq])
            key = (op, round(thr, 10))
            if not np.isfinite(thr) or key in seen:
                continue
            seen.add(key)
            atoms.append(Atom(feature, op, thr, f"Q{int(round(qq * 100)):02d}", family))
    return atoms


def _compatible(atoms: list[Atom]) -> bool:
    features = [a.feature for a in atoms]
    families = [a.family for a in atoms]
    return len(features) == len(set(features)) and len(families) == len(set(families))


def _score(stats: dict) -> float:
    if stats.get("verdict") in {"NO_EVALUABLE_ROWS", "INSUFFICIENT_ECON_SAMPLE", "ECON_NEGATIVE"}:
        return float("-inf")
    mean = float(stats.get("mean_pnl_1mw_eur_per_mtu", 0.0))
    med = float(stats.get("median_pnl_1mw_eur_per_mtu", 0.0))
    days = max(1, int(stats.get("n_days", 0)))
    pf = float(stats.get("profit_factor", 0.0))
    robust_center = 0.65 * mean + 0.35 * med
    return robust_center * math.sqrt(days) * max(0.5, min(pf, 3.0))


def discover(train: pd.DataFrame, feature_registry: pd.DataFrame, max_depth: int = 4, beam_width: int = 80) -> list[dict]:
    """Train-only adaptive search inspired by the UK deep discovery engine.

    The target is direct gross PnL, never NIV/sign alone. Candidate thresholds are
    derived only from the train slice. Search expands 1..max_depth factors while
    forbidding duplicate feature families.
    """
    atoms = generate_atoms(train, feature_registry)
    singles = []
    for a in atoms:
        m = mask_atoms(train, [a])
        st = summarize_pnl(train.loc[m])
        sc = _score(st)
        if np.isfinite(sc):
            singles.append((sc, [a], st))
    singles.sort(key=lambda x: -x[0])
    beam = singles[:beam_width]
    out = []
    seen: set[str] = set()

    def emit(aa: list[Atom], st: dict, sc: float):
        definition = json.dumps([asdict(a) for a in aa], sort_keys=True)
        if definition in seen:
            return
        seen.add(definition)
        out.append({"definition_json": definition, "n_factors": len(aa), "train_score": float(sc), **st})

    for sc, aa, st in beam:
        emit(aa, st, sc)

    anchor_atoms = [x[1][0] for x in singles[: min(len(singles), 160)]]
    for depth in range(2, max_depth + 1):
        nxt = []
        for _, base, _ in beam:
            for a in anchor_atoms:
                aa = base + [a]
                if not _compatible(aa):
                    continue
                m = mask_atoms(train, aa)
                st = summarize_pnl(train.loc[m])
                sc = _score(st)
                if np.isfinite(sc):
                    nxt.append((sc, aa, st))
        nxt.sort(key=lambda x: -x[0])
        dedup = []
        defs = set()
        for item in nxt:
            d = json.dumps([asdict(a) for a in item[1]], sort_keys=True)
            if d in defs:
                continue
            defs.add(d)
            dedup.append(item)
            if len(dedup) >= beam_width:
                break
        beam = dedup
        for sc, aa, st in beam:
            emit(aa, st, sc)
    return out


def _bh(rows: list[dict], alpha: float = 0.10) -> None:
    ids = [(i, r.get("daily_sign_pvalue")) for i, r in enumerate(rows) if r.get("daily_sign_pvalue") is not None and np.isfinite(r.get("daily_sign_pvalue"))]
    ids.sort(key=lambda z: z[1])
    m = len(ids)
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i, p = ids[rank]
        q = min(prev, p * m / (rank + 1))
        prev = q
        rows[i]["qvalue"] = float(q)
        rows[i]["fdr_pass"] = bool(q <= alpha)


def validate(panel: pd.DataFrame, candidates: list[dict], holdout_from: str, alpha: float = 0.10) -> list[dict]:
    d = panel.copy()
    ts = pd.to_datetime(d["delivery_start_utc"], utc=True, errors="coerce")
    holdout = ts >= pd.Timestamp(holdout_from, tz="UTC")
    out = []
    for c in candidates:
        atoms = [Atom(**a) for a in json.loads(c["definition_json"])]
        m = mask_atoms(d, atoms) & holdout
        st = summarize_pnl(d.loc[m])
        row = {"definition_json": c["definition_json"], "n_factors": c["n_factors"], "train_score": c["train_score"], **{f"oos_{k}": v for k, v in st.items()}}
        row["daily_sign_pvalue"] = st.get("daily_sign_pvalue")
        out.append(row)
    _bh(out, alpha=alpha)
    for r in out:
        robust = (
            r.get("oos_verdict") == "ECON_POSITIVE_ROBUSTNESS_SCREEN"
            and int(r.get("oos_n_days", 0)) >= 10
            and int(r.get("oos_n_mtu", 0)) >= 30
            and float(r.get("oos_positive_month_fraction", 0.0)) >= 0.60
            and bool(r.get("fdr_pass", False))
        )
        r["machine_status"] = "REVIEW_READY" if robust else "VALIDATING"
    return out


def run_profit_lab(panel_path: Path, feature_registry_path: Path, out_path: Path, holdout_from: str) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not panel_path.exists():
        payload = {"status": "BLOCKED_MISSING_PROFIT_PANEL", "reason": "Certified entry-price + imbalance panel not available yet", "candidates": []}
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    panel = pd.read_parquet(panel_path)
    required = {"delivery_start_utc", "entry_price", "imbalance_price", "system_view", "gross_pnl_1mw_eur"}
    missing = sorted(required - set(panel.columns))
    if missing:
        payload = {"status": "BLOCKED_BAD_PROFIT_PANEL", "missing": missing, "candidates": []}
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    reg = pd.read_csv(feature_registry_path)
    if "pit_status" in reg:
        reg = reg[reg["pit_status"].astype(str).str.upper().eq("CERTIFIED")]
    ts = pd.to_datetime(panel["delivery_start_utc"], utc=True, errors="coerce")
    train = panel.loc[ts < pd.Timestamp(holdout_from, tz="UTC")].copy()
    cands = discover(train, reg)
    vals = validate(panel, cands, holdout_from)
    vals.sort(key=lambda r: (r["machine_status"] != "REVIEW_READY", -float(r.get("oos_total_pnl_1mw_eur", -1e18))))
    payload = {
        "status": "PASS" if vals else "PASS_NO_CANDIDATES",
        "objective": "DIRECT_GROSS_PNL_ENTRY_TO_IMBALANCE_PT15",
        "holdout_from": holdout_from,
        "candidate_count": len(vals),
        "review_ready_count": sum(r["machine_status"] == "REVIEW_READY" for r in vals),
        "candidates": vals[:200],
        "policy": "No promotion from sign/NIV alone; direct economic OOS evidence required.",
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
