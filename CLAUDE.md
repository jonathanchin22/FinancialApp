# FinancialApp — notes for Claude

Read docs/PLAN.md for the vision, data model, architecture and phased roadmap. Work phase by phase; don't build later-phase features before the current phase's gate passes.

Rules:
- All SQL lives in `src/finapp/repository.py` (plus migrations). Schema changes = a new numbered file in `src/finapp/db/migrations/`; never edit an applied migration.
- Money is integer minor units + currency. Never use floats for money; parse with `finapp.money`.
- New data sources implement the `Adapter` protocol in `src/finapp/ingest/base.py` and go through `run_import`.
- Never commit real financial data. `data/` and `*.db` are git-ignored; test fixtures use made-up numbers.
- Run `pytest` before committing.
