# FinancialApp

A local-first, multi-currency personal finance system. Your data stays in one SQLite file on your own machine; bank data comes in through pluggable adapters, so no single provider is a point of failure.

Built for a US tax resident with accounts in USD and CAD: net worth is FX-aware, and foreign accounts are tracked against FBAR and Form 8938 thresholds.

See [docs/PLAN.md](docs/PLAN.md) for the full plan and roadmap.

## Status: Phase 0 (Foundation)

- SQLite schema for the full data model, with a forward-only migrator
- Repository layer (the only code that writes SQL)
- Ingestion adapter contract + CSV and OFX/QFX adapters
- Cross-source deduplication (same transaction from CSV and OFX is stored once)
- Sync state per account; failures are recorded, never silent
- Bank of Canada daily USD/CAD history, exact Decimal conversion
- Balance snapshots, net worth in any currency, FBAR / Form 8938 check
- CLI and FastAPI endpoints

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[api,dev]'

finapp init
finapp fx-sync                                   # USD/CAD history from the Bank of Canada
finapp import-csv examples/sample_us_checking.csv \
    --account-key csv:chase:checking --name "Chase Checking" \
    --type checking --currency USD --institution Chase --country US
finapp import-ofx ~/Downloads/td.qfx --country CA  # OFX/QFX from any bank
finapp accounts
finapp networth --currency USD
finapp foreign 2026                               # FBAR / Form 8938 check

uvicorn finapp.api:app --reload                   # API at http://127.0.0.1:8000/docs
pytest
```

The database lives at `data/finapp.db` (override with `--db` or `FINAPP_DB`). `data/` is git-ignored; never commit financial data.

## CSV imports

Columns are auto-detected from common header names (`Date`/`Posting Date`, `Description`/`Payee`, `Amount` or `Debit`/`Credit`, `Balance`). The date format is chosen as the first one that parses every row, which resolves day-first vs month-first. Override anything with `--date-col`, `--amount-col`, `--date-format`, etc. Use `--negate` for exports that show purchases as positive numbers (common for credit cards).

A `Balance` column becomes daily balance snapshots, which drive net worth and the foreign-account peak tracker.

## Conventions

- Money is stored as integer minor units (cents) in the account's own currency; negative = money out.
- Every transaction has at least one split; category, flow type (spend, income, transfer, investment, refund) and budget inclusion live on the split.
- Deduplication hashes (account, date, amount, currency, occurrence), not the descriptor, because sources describe the same purchase differently.
- FX rates are stored as `1 base = rate quote`. Bank of Canada rates use source `boc`; store the Treasury year-end rate with `finapp fx-set 2026-12-31 <CAD per USD>` for the official FBAR conversion.

## Project layout

```
src/finapp/
  db/migrations/   numbered SQL migrations
  ingest/          adapter contract, CSV, OFX, import service
  repository.py    all SQL
  fx.py            Bank of Canada client, conversion
  reports.py       net worth, foreign-account report
  api.py           FastAPI app
  cli.py           command line
tests/
```
