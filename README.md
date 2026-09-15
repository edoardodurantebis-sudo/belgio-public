# Belgium Public Data Factory + Market Intelligence Lab

Public-only, reproducible research infrastructure for the Belgian power market.

## Scientific contract

This repository is deliberately isolated from Duferco/internal systems. It may ingest only public/official sources (primarily Elia, JAO and, where useful, ENTSO-E) plus data explicitly supplied to this public project.

The architecture separates:

1. **raw/vintage evidence** — provider payload + retrieval timestamp + URL + SHA256 + response metadata;
2. **canonical tables** — normalised delivery timestamps and stable provenance columns;
3. **health/PIT gates** — schema, time, duplicate, availability and structural-break checks;
4. **market tape** — ex-post reconstruction of Belgian system physics and imbalance-price formation;
5. **research lab** — only certified inputs, with temporal holdout/walk-forward and no silent leakage.

A positive in-sample PnL is never sufficient evidence. If required data/timing/schema is not proven, the correct output is **BLOCKED / INSUFFICIENT EVIDENCE**.

## Hard structural breaks

- **22 May 2024 — MARI local go-live:** pre/post imbalance and balancing regimes are kept separate unless an explicit mapping is scientifically certified.
- **22 May 2024 — ICAROS:** generation/unavailability publications around the cutover are treated as potentially non-homogeneous.
- **8 June 2022 — Core FB DA go-live:** Core flow-based capacity analysis uses the JAO Core Publication Tool from this regime onward.

## Repository layout

```text
config/
  sources.json                 central source registry
  feature_availability.json    ex-ante/PIT eligibility registry
src/belgium_public/
  elia.py                      Elia collector
  jao.py                       verified JAO API canary
  provenance.py                immutable-ish raw evidence metadata
  canonical.py                 canonicalisation/idempotent merges
  health.py                    fail-closed data gates
  market_tape.py               ex-post long-form tape builder
  lab.py                       research gate; no premature edge promotion
scripts/
  run_factory.py
  run_market_tape.py
  run_lab.py
  restore_latest_state.sh      restores cumulative Actions state
ledger/RESEARCH_LEDGER.md       persistent scientific memory
state/                          machine-readable run/health state
research/                       market-tape/lab outputs
.github/workflows/
  ci.yml
  factory.yml                  historical bootstrap/incremental refresh
  nrt.yml                      prospective 15-minute vintage capture
```

Large public datasets are not committed as source code. GitHub Actions carries the cumulative `data/raw`, `data/canonical`, `state` and `research` layers forward as a rolling `belgium-public-state` artifact. Raw payloads are gzip-compressed and accompanied by provenance sidecars.

## Local/CI entry points

```bash
python -m pip install -e .[dev]
pytest -q
python scripts/run_factory.py --mode bootstrap
python scripts/run_factory.py --mode incremental
python scripts/run_factory.py --mode nrt
python scripts/run_market_tape.py --date YYYY-MM-DD
python scripts/run_lab.py
```

`run_lab.py` is intentionally fail-closed: it does not promote research candidates unless the core health gate passes and feature availability is certified for the intended decision time.
