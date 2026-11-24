import pytest

from conftest import SGML_OFX

fastapi = pytest.importorskip("fastapi")


def test_api_import_and_accounts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import finapp.api as api
    from finapp.db import connect
    from finapp.repository import Repository

    monkeypatch.setattr(api, "_repo", Repository(connect(tmp_path / "api.db")))
    client = TestClient(api.app)
    r = client.post("/import/ofx", files={"file": ("td.ofx", SGML_OFX)})
    assert r.status_code == 200 and r.json()["inserted"] == 2
    accts = client.get("/accounts").json()
    assert accts[0]["freshness"] == "fresh" and accts[0]["is_foreign"] == 1
