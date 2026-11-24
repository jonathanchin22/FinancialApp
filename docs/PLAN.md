# FinancialApp — Project Plan

As of 2026-11-23. Living version (with diagrams): https://claude.ai/artifact/3zPbTGDsAjJ1gd4a7EPza1

## Vision and principles
A personal, local-first finance system that stays accurate without daily upkeep. Built first for one user (Canadian on a TN in the US; money in USD and a little CAD), with a path to release later.

1. Never fail silently: every account shows data freshness; stale data is flagged.
2. Accuracy over inboxes: categories right on arrival; review is only for rare low-confidence exceptions.
3. Model money as it moves: category, flow type (spend/income/transfer/investment/refund) and budget inclusion are separate fields.
4. Multi-currency and cross-border from day one: CAD and USD first-class, FX-aware net worth, compliance tracking.
5. Own the data: stored locally; no single ingestion source is a point of failure.
6. Useful in ten minutes: one account to first insight.

## Pain points → features
| Pain point | Feature | Phase |
|---|---|---|
| Sync breaks silently | Per-account freshness badge, stale alerts, CSV/OFX fallback, multiple sources | 1 |
| Wrong categories | Layered categorization engine | 1 |
| Uncategorized backlog | Engine accuracy + exceptions-only review | 1 |
| Transfers counted as spending | Flow type + budget inclusion as separate fields | 1 |
| Single currency | Multi-currency ledger, FX history, foreign-balance peak tracker (FBAR $10k) | 1 |
| Clumsy splits | Editable splits by amount or %, per-split inclusion | 2 |
| Can't plan ahead | Forward budgets, YTD actual vs plan | 2 |
| Onboarding overload | One account to first insight | 2 |
| Data lags 6–24 h | Pending transactions, optional alert-email parser | 3 |
| Noisy alerts | Predictive alerts (bills vs balance, budget pace) | 3 |

## Categorization engine (target ≥95% correct on arrival)
1. Merchant normalization (clean descriptors → canonical merchant ID, fuzzy match)
2. Hard signals: MCC code + aggregator category
3. Shared merchant knowledge base (seeded from open data/aggregator; grows from anonymized corrections — the crowd-sourced layer)
4. Personal learned model (account, amount band, day, recurrence; retrains locally on each correction)
5. Local LLM fallback for unknown merchants; writes back to the knowledge base

Confidence ≥0.9 → auto-apply; below → exception queue with best guess prefilled. Corrections update layers 3–4 and re-apply retroactively. Track auto-accept and correction rates weekly; raise threshold if corrections on auto-accepted items exceed 2%.

## Core data model
Institution, Account (currency, jurisdiction, is_foreign), SyncState, Transaction (original currency, raw_descriptor, merchant_id, source_hash for dedup; never overwritten), Split (amount or pct, category, flow_type, budget_include, tags; ≥1 per transaction), Merchant, Category (≤3 levels), Rule/Model (conditions, action, confidence, origin), FxRate (daily; Treasury year-end rate for FBAR), Budget (plannable ahead, rollover), BalanceSnapshot. Transfers matched in pairs.

Implemented in `src/finapp/db/migrations/001_initial.sql`.

## Architecture and stack
Single-user web app on Jon's own machine, reached from desktop/phone over Tailscale.
- Frontend: SvelteKit or React (Vite), installable PWA
- API: Python + FastAPI (read/write)
- Storage: SQLite (WAL) behind a repository layer; Postgres later
- Ingestion: adapter plugins (aggregator APIs, CSV, OFX/QFX) implementing list_accounts(), fetch_transactions(since), fetch_balances(); report health to SyncState
- Jobs: APScheduler (sync, FX, snapshots, retraining)
- Categorization: rapidfuzz, scikit-learn/LightGBM, Ollama
- FX: Bank of Canada Valet API
- AI access: read-only MCP server over SQLite (DataBridge pattern) for ARIA / local LLMs

## Roadmap
- **Phase 0 · Foundation** (in progress): schema, repository layer, FastAPI skeleton; adapter interface + CSV/OFX import with dedup; FX history; balance snapshots. Gate: one month of real USD+CAD transactions imported, deduplicated, FX-converted.
- **Phase 1 · Trustworthy core**: live sync via first 1–2 providers with freshness badges/alerts; categorization layers 1–4; flow types, transfer matching, multi-currency net worth, foreign-balance peak tracker; read-only MCP server. Gate: ≥95% auto-categorized for 4 straight weeks; no account stale unflagged.
- **Phase 2 · Planning and polish**: editable splits; forward budgets, YTD vs plan; LLM fallback; PWA + ten-minute onboarding. Gate: full monthly review under 10 minutes.
- **Phase 3 · Intelligence and release prep**: predictive alerts; pending transactions + alert-email parser; investment holdings; release decision (auth, Postgres, shared merchant base).

## Decisions (2026-11-23)
Web app first · single-user with path to release · pluggable low-cost bank data sources · read-only MCP data layer.

## Open decisions
- Which 1–2 ingestion providers; confirm coverage of US bank, US cards, Canadian chequing
- SvelteKit vs React
- Always-on host machine
- Investments: balances only, or holdings/performance
- Seed datasets for the merchant knowledge base

## Risks
Provider breakage (adapters + CSV fallback + alerts) · poor Canadian coverage (CSV/OFX) · data on home machine (encrypted disk, encrypted backups, Tailscale-only, read-only MCP) · accuracy <95% (weekly metrics) · scope creep (phase gates).
