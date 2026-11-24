"""HTTP API (FastAPI). Install with: pip install -e '.[api]'  then: uvicorn finapp.api:app"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile

from finapp import __version__
from finapp.db import connect
from finapp.fx import sync_boc
from finapp.ingest import CsvAdapter, CsvMapping, OfxAdapter, run_import
from finapp.models import AccountInfo, AccountType
from finapp.reports import foreign_account_report, net_worth
from finapp.repository import Repository

STALE_AFTER_HOURS = 72

app = FastAPI(title="FinancialApp", version=__version__)
_repo: Repository | None = None


def get_repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository(connect())
    return _repo


RepoDep = Annotated[Repository, Depends(get_repo)]


def _freshness(last_success_at: str | None) -> str:
    if not last_success_at:
        return "never"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(last_success_at)
    return "stale" if age.total_seconds() > STALE_AFTER_HOURS * 3600 else "fresh"


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/accounts")
def accounts(repo: RepoDep):
    out = repo.list_accounts()
    for a in out:
        a["freshness"] = _freshness(a["last_success_at"])
    return out


@app.get("/transactions")
def transactions(repo: RepoDep, account_id: int | None = None, start: date | None = None,
                 end: date | None = None, limit: int = 200):
    return repo.list_transactions(account_id, start, end, min(limit, 2000))


@app.post("/import/csv")
async def import_csv(
    repo: RepoDep,
    file: UploadFile = File(...),
    account_key: str = Form(...),
    account_name: str = Form(...),
    account_type: AccountType = Form(...),
    currency: str = Form(...),
    institution: str = Form(...),
    country: str = Form(...),
    negate: bool = Form(False),
    date_format: str | None = Form(None),
):
    text = (await file.read()).decode("utf-8-sig")
    info = AccountInfo(external_key=account_key, name=account_name, type=account_type,
                       currency=currency, institution=institution, country=country)
    try:
        adapter = CsvAdapter(None, info, CsvMapping(negate=negate, date_format=date_format), text=text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    res = run_import(repo, adapter)
    return {"inserted": res.inserted, "duplicates": res.duplicates, "accounts": [a.__dict__ for a in res.accounts]}


@app.post("/import/ofx")
async def import_ofx(repo: RepoDep, file: UploadFile = File(...), country: str | None = Form(None),
                     institution: str | None = Form(None)):
    text = (await file.read()).decode("utf-8", errors="replace")
    try:
        adapter = OfxAdapter(text=text, country=country, institution=institution)
    except ValueError as e:
        raise HTTPException(422, str(e))
    res = run_import(repo, adapter)
    return {"inserted": res.inserted, "duplicates": res.duplicates, "accounts": [a.__dict__ for a in res.accounts]}


@app.post("/fx/sync")
def fx_sync(repo: RepoDep):
    return {"rates_stored": sync_boc(repo)}


@app.get("/net-worth")
def get_net_worth(repo: RepoDep, on: date | None = None, currency: str = "USD"):
    return net_worth(repo, on or date.today(), currency).__dict__


@app.get("/foreign-accounts/{year}")
def foreign_accounts(repo: RepoDep, year: int):
    return foreign_account_report(repo, year)
