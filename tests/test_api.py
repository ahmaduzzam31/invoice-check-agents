"""Tests for the FastAPI service and the audit log (no model needed: only the parts that refuse bad
input or read stored data). Uses temporary databases, never the real ones."""

import pytest
from fastapi.testclient import TestClient

from src import audit, config


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHECKPOINT_DB", tmp_path / "checkpoints.db")
    monkeypatch.setattr(config, "AUDIT_DB", tmp_path / "audit.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    from src.api import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["simulated_data"] is True


def test_invoice_list_never_shows_the_answer_key(client):
    if not config.INVOICE_DIR.exists():
        pytest.skip("simulated invoices missing: run scripts/generate_data.py")
    rows = client.get("/invoices").json()
    assert len(rows) == 60
    assert set(rows[0]) == {"invoice_id", "file"}


@pytest.mark.parametrize("bad, code", [("INV999", 404), ("invoice_001", 422), ("INV1", 422)])
def test_bad_invoice_ids_are_refused(client, bad, code):
    assert client.post(f"/invoices/{bad}/check").status_code == code


def test_non_pdf_upload_is_refused(client):
    r = client.post("/uploads/check", files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 415


def test_oversized_upload_is_refused(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/uploads/check", files={"file": ("x.pdf", b"%PDF-1.4 " + b"0" * 50, "application/pdf")})
    assert r.status_code == 413


def test_decision_on_unknown_run_is_refused(client):
    r = client.post("/runs/no-such-run/decision", json={"decision": "approve", "by": "Tester"})
    assert r.status_code == 404


def test_invalid_decision_value_is_refused(client):
    r = client.post("/runs/no-such-run/decision", json={"decision": "maybe", "by": "Tester"})
    assert r.status_code == 422


def test_audit_log_records_checks_and_decisions(client):
    out = {"thread_id": "t1", "invoice_id": "INV001", "final_status": "waiting_for_person", "note": "n",
           "result": {"decision": "flag", "problems": ["duplicate"], "money_at_risk_net": 10.0, "checks": []}}
    audit.record_check(out)
    audit.record_decision({**out, "final_status": "rejected_by_person",
                           "human": {"decision": "reject", "by": "Tester", "comment": "dup"}})
    rows = client.get("/audit", params={"invoice_id": "INV001"}).json()
    assert [r["event"] for r in rows] == ["human_decision", "checked"]
    assert rows[0]["actor"] == "Tester" and rows[1]["problems"] == ["duplicate"]
