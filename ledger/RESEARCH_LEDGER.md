# Belgium Public — Scientific Research Ledger

This file is the persistent memory of the project. Entries are append-only in spirit: corrections should explain what changed and why.

## 2026-09-15 — Repository bootstrap

### Scope lock
- Public/official data only. No Duferco DB, internal feeds, proprietary files, or private operational information.
- Raw vintages, canonical tables, health state, market tape and research outputs are separate layers.
- Discovery is blocked by health/PIT gates; positive in-sample PnL is never a promotion criterion.

### Official Elia sources verified from public documentation
- **ODS134**: post-MARI quarter-hour imbalance prices, from 22/05/2024; daily historical refresh; initially non-validated, month M validated after 15th of M+1.
- **ODS047**: pre-MARI quarter-hour imbalance prices through 21/05/2024; contains separate positive/negative BRP imbalance tariffs.
- **ODS127**: post-MARI quarter-hour balancing energy volume components requested by Elia to offset control-area imbalance.
- **ODS132**: activated balancing volumes in Belgium for Elia or other TSOs.
- **ODS166**: historical post-MARI quarter-hour balancing-energy price components used to explain imbalance-price formation.
- **ODS167**: near-real-time quarter-hour balancing-volume components, current day, refreshed each quarter-hour. Historical endpoint does not prove trader knowledge time; prospective snapshots required.
- **ODS168**: near-real-time quarter-hour balancing-price components, current day, refreshed every 15 min.
- **ODS175**: near-real-time 1-minute balancing-price components, current hour, refreshed every minute. Datetime represents end of the calculation minute for the current quarter-hour estimate.
- **ODS013 / ODS205**: intraday available capacity at last closed gate, historical / near-real-time. Do not mark PIT-safe until exact gate/publication semantics are field-validated.
- **ODS001 / ODS002**: total load historical / near-real-time. Historical labelled DA/week-ahead forecast fields can be assessed at field level; historical “most recent” is not assumed PIT-safe.
- **ODS031 / ODS086**: wind historical / near-real-time. ODS031 has explicit warning about measured/upscaled offshore deltas during 2018–2023.
- **ODS032 / ODS087**: PV historical / near-real-time.
- **ODS026 / ODS160**: physical cross-border flow historical / near-real-time; positive = Belgian export, negative = import.
- **ODS201**: total actual generation aggregated by fuel type.
- **ODS202**: technical-unit forced/planned unavailabilities >100 MW; versioned outage data is a candidate for PIT reconstruction using last-updated/version, not certified yet.

### Structural breaks
- **22/05/2024 MARI local go-live**: hard break for imbalance/balancing definitions. Pre/post samples remain separate until a scientific mapping is proven.
- **22/05/2024 ICAROS local go-live**: generation/outage publications around this date require schema/methodology checks.
- **08/06/2022 Core FB DA go-live**: JAO Core Publication Tool supersedes old CWE-style publications for Core DA flow-based analysis.
- **25/06/2025 IDCC(c) go-live** and **28/04/2026 IDCC(d) go-live** are relevant intraday-capacity regime dates and must be encoded before pooling IDCC history.

### JAO / Core finding
Official JAO documentation confirms a public HTTPS/JSON GET web service and explicitly documents the Max Exchanges endpoint `https://publicationtool.jao.eu/core/api/core/maxExchanges/index`; timestamps/date parameters are UTC. The current handbook documents Final/Pre-Final domain fields including CNE/CNEC identity, RAM, FRM, Fmax, F0/Fref components and one PTDF column per Core hub. It gives publication time 08:00 D-1 for Pre-Final and 10:30 D-1 for Final Computation. **The exact current endpoint path/schema for the CNEC/RAM/PTDF domain has not yet been independently verified in this bootstrap and therefore remains blocked rather than guessed.**

### Initial scientific status
- Ex-post post-MARI mechanism reconstruction is feasible once ODS134 + ODS166 + ODS127/132 are downloaded and aligned.
- Historical ex-ante discovery using “most recent forecast” revisions is **not** yet PIT-safe because historical tables do not by themselves prove the vintage available at each commercial decision time.
- The NRT snapshot collector family is intentionally included to build a proper prospective vintage store for load/wind/solar/balancing/capacity.
- No trading edge is claimed at bootstrap.

### Next investigations
1. Bootstrap Elia core sources and record actual coverage/schema/health from GitHub Actions.
2. Verify JAO CNEC-domain endpoint(s), then backfill Core Final / Pre-Final data and encode publication timing.
3. Add ENTSO-E Transparency only where it adds independent coverage/provenance vs Elia, avoiding duplicate complexity.
4. Build first real market-tape case studies: large positive/negative system imbalance, extreme imbalance price, renewable forecast miss, and import/export constraint episodes.
5. Only after PIT registry is green: residual-load / forecast-revision / congestion regime discovery with temporal holdout and walk-forward.

## 2026-09-15 — First green end-to-end public factory + hardening findings

### End-to-end run
- GitHub Actions run **34951767576** completed successfully: historical collector, canonical build, health, live JAO endpoint audit, case-study registry, lab gate and artifact upload all passed operationally.
- Artifact `belgium-public-state` id **10389309014**: 423,901,579 bytes compressed; GitHub digest `sha256:c433db148fee667c4bf4bda0af418c3c0565555bdf308e44c8e878acd2973dab`.
- The original monolithic export design was retired. Historical Elia bootstrap is bounded/resumable; state-writing workflows are serialized through one concurrency lock.

### Actual canonical coverage measured from the artifact
- **ODS134** post-MARI imbalance: 81,216 QH; 2024-05-21 22:00 UTC → 2026-09-14 21:45 UTC.
- **ODS047** pre-MARI imbalance: 329,180 QH; 2014-12-31 23:00 UTC → 2024-05-21 21:45 UTC.
- **ODS127** balancing-volume components: 81,216 QH; same post-MARI span as ODS134.
- **ODS132** activated volumes: 81,193 QH; starts 15 minutes later than ODS134 and contains 23 fewer distinct timestamps. This is now explicitly surfaced by health-v2 rather than hidden by a file-exists check.
- **ODS166** balancing-price components: 81,216 QH; same post-MARI span as ODS134.
- **ODS001** load: 411,164 rows; 2014-12-31 23:00 UTC → forecast horizon 2026-09-22 21:45 UTC.
- **ODS031** wind: 1,235,796 rows / 411,932 distinct delivery timestamps; 2014-12-31 23:00 UTC → forecast horizon 2026-09-30 21:45 UTC.
- **ODS032** PV: 2,963,520 rows / 211,680 distinct delivery timestamps; 2020-08-31 22:00 UTC → 2026-09-14 21:45 UTC.
- **ODS026** physical cross-border flow: 1,617,684 rows / 410,396 distinct delivery timestamps; 2014-12-31 23:00 UTC → 2026-09-14 21:45 UTC.
- **ODS013** intraday capacity historical: 103,488 rows / 51,744 distinct timestamps; 2024-12-31 23:00 UTC → 2026-09-14 21:45 UTC. Its historical PIT semantics remain unproved.

### Correction — JAO Core endpoint is now verified
The bootstrap entry above said the exact current CNEC/RAM/PTDF endpoint was still unverified. That uncertainty is now closed.

A live fail-closed audit on 2026-09-15 verified:
- endpoint: `https://publicationtool.jao.eu/core/api/data/finalComputation`
- HTTP 200 with `FromUtc` / `ToUtc` pagination contract;
- schema contains `ram`, CNEC/CNE identity fields and multiple `ptdf_*` hub columns.

The legacy path family used by the original Max Exchanges canary returned HTTP 400 requiring `FromUtc` and `ToUtc`; it is therefore removed from the logical registry and replaced by the verified Final Computation source. Endpoint/schema verification makes Core FB data **mechanism-ready ex-post**, but does **not** prove historical ex-ante knowledge time. Scheduled handbook times remain distinct from observed publication timestamps.

### Additional official Elia mechanism sources registered
- **ODS133** historical minute imbalance price; **ODS161** live minute imbalance price; **ODS162** live QH imbalance price.
- **ODS165** historical minute balancing-price components; **ODS174** live minute balancing-volume components; **ODS135** live activated volumes.
- **ODS015/016** DA/final commercial cross-border schedules; **ODS014** long-term capacity.
- **ODS153** available balancing-energy prices.
- **ODS156** post-MARI individual incremental balancing bids; **ODS068/069** pre-MARI individual incremental/decremental bids.
- **ODS064** pre-MARI activated balancing-energy prices.
These are extended/lab inputs until their schema and publication semantics are certified; registering them does not imply PIT safety.

### Additional Core intraday structural breaks
- **29/05/2024 — IDCC(b) go-live**.
- **25/06/2025 — IDCC(c) go-live**.
- **28/04/2026 — IDCC(d) go-live**.
Documented fallback/incident business days must be flagged rather than pooled as ordinary observations.

### Incremental-watermark bug found and fixed
Historical forecast tables can contain delivery timestamps beyond “now”. Using maximum delivery timestamp as an incremental watermark therefore risks skipping revisions and actual values in the present. Factory logic now uses overlapping cursors:
- mixed forecast/actual or versioned data: `min(max_delivery, now) - 30d`;
- immutable-ish outcome histories: small 2-day overlap;
- prospective vintage families preserve retrieval time separately.
This keeps updates idempotent while catching corrections/revisions.

### Health gate v2
The first artifact's health-v1 status was operationally green but not strict enough to be the final scientific certificate. Health-v2 now records and gates:
- schema fingerprint and cross-run schema drift;
- expected vs observed fixed granularity;
- coverage ratio, missing intervals and maximum gap;
- off-grid timestamps and duplicate keys;
- post-MARI tape joinability across ODS134/127/132/166.
The lab remains fail-closed if this gate fails.

### First reproducible extreme cases from ODS134
These are **case-study selectors, not trading rules**:
- 2025-02-08 10:45 UTC: SI +1,351.395 MW; imbalance price -450 EUR/MWh.
- 2024-06-29 15:45 UTC: SI -1,681.317 MW; imbalance price +400 EUR/MWh.
- 2026-08-15 09:45 UTC: SI -72.442 MW; imbalance price +3,114.671 EUR/MWh.
- 2026-04-06 around 11:45–12:45 UTC: several QH at -15,000 EUR/MWh with positive SI; prime candidate for a first deep market-tape explanation.

### Scientific status after first green factory
- **Mechanism-ready:** post-MARI imbalance/activation/price-component tape; verified JAO Core CNEC/RAM/PTDF endpoint for ex-post congestion analysis.
- **Prospective PIT only:** live/revised load, wind, solar, NRT balancing, NRT imbalance and NRT capacity vintages until enough history is accumulated.
- **Still blocked for ex-ante claims:** historical “most recent” forecast revisions; JAO RAM/PTDF features without observed historical publication timestamps; any bid-stack feature before its publication timing is certified.
- **Edges:** none promoted. No robust/watchlist rule exists yet solely because the first factory is operational.

### Next investigations
1. Promote health-v2 after CI and rerun against the real artifact.
2. Progressively backfill verified JAO Final Computation history from recent data toward 08/06/2022 while refreshing recent days every run.
3. Accumulate NRT vintages and certify field-level availability for the actual decision gates.
4. Deep-dive 2026-04-06 and 2024-06-29 with full market tape, then add renewable/import/congestion explanatory layers.
5. Begin regime discovery only on features whose PIT status is proven; use temporal holdout/walk-forward and keep rejected hypotheses in this ledger.
