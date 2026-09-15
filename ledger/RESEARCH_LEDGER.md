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
