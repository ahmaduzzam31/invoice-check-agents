"""FastAPI service for the invoice checking agents.

Run:  .venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/docs (try every endpoint in the browser)

All data is SIMULATED. Checking an invoice takes about 40 to 60 seconds on a laptop with a local model.
"""

import re
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from src import audit, config
from src.graph import build_graph, check_invoice, decide

INVOICE_ID = re.compile(r"^INV\d{3}$")
THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as checkpointer:
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(title="Invoice Check Agents API", version="1.0.0", lifespan=lifespan,
              description="Multi-agent invoice checks against purchase orders, deliveries and contract prices. "
                          "All data is SIMULATED. Flagged invoices wait for a person's decision.")


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    by: str = Field(min_length=1, max_length=80, description="Name of the person deciding")
    comment: str = Field(default="", max_length=500)


def summary(out: dict) -> dict:
    r = out.get("result") or {}
    return {"thread_id": out.get("thread_id"), "invoice_id": out.get("invoice_id"),
            "paused_for_person": bool(out.get("paused")), "final_status": out.get("final_status"),
            "decision": r.get("decision"), "problems": r.get("problems", []), "unverified": r.get("unverified", []),
            "money_at_risk_net": r.get("money_at_risk_net"), "note": out.get("note"),
            "checks": r.get("checks", []), "extraction": out.get("extraction"), "human": out.get("human"),
            "steps": [{k: s[k] for k in ("agent", "detail", "seconds")} for s in out.get("steps", [])],
            "seconds": out.get("seconds")}


async def _run(invoice_id: str, pdf_path) -> dict:
    thread = f"{config.DATA_SET}-{invoice_id}-{uuid.uuid4().hex[:8]}"
    out = await check_invoice(app.state.graph, invoice_id, str(pdf_path), thread)
    audit.record_check(out)
    return summary(out)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "data_set": config.DATA_SET, "model": f"{config.LLM_PROVIDER}:{config.LLM_MODEL}",
            "simulated_data": True}


@app.get("/invoices")
async def list_invoices() -> list[dict]:
    """The simulated invoices available to check (the answer key is never exposed)."""
    return [{"invoice_id": f"INV{p.stem.split('_')[1]}", "file": p.name}
            for p in sorted(config.INVOICE_DIR.glob("invoice_*.pdf"))]


@app.post("/invoices/{invoice_id}/check")
async def check_simulated_invoice(invoice_id: str) -> dict:
    if not INVOICE_ID.match(invoice_id):
        raise HTTPException(422, "invoice_id must look like INV001")
    path = config.INVOICE_DIR / f"invoice_{invoice_id[3:]}.pdf"
    if not path.exists():
        raise HTTPException(404, f"{invoice_id} not found")
    return await _run(invoice_id, path)


@app.post("/uploads/check")
async def check_uploaded_pdf(file: UploadFile = File(...)) -> dict:
    """Upload an invoice PDF (up to 5 MB) and check it."""
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file is larger than 5 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "only PDF files are accepted")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = f"UPLOAD-{uuid.uuid4().hex[:8]}"
    path = config.UPLOAD_DIR / f"{upload_id}.pdf"  # our own file name, never the uploaded one
    path.write_bytes(data)
    return await _run(upload_id, path)


async def _snapshot(thread_id: str):
    if not THREAD_ID.match(thread_id):
        raise HTTPException(422, "invalid thread_id")
    snap = await app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not snap.values:
        raise HTTPException(404, f"no run with thread_id {thread_id}")
    return snap


@app.get("/runs/{thread_id}")
async def get_run(thread_id: str) -> dict:
    snap = await _snapshot(thread_id)
    return summary({**snap.values, "thread_id": thread_id, "paused": bool(snap.next)})


@app.post("/runs/{thread_id}/decision")
async def decide_run(thread_id: str, body: Decision) -> dict:
    """A person approves or rejects a flagged invoice that is waiting for a decision."""
    snap = await _snapshot(thread_id)
    if not snap.next:
        raise HTTPException(409, "this invoice is not waiting for a decision")
    out = await decide(app.state.graph, thread_id, body.decision, body.by, body.comment)
    audit.record_decision(out)
    return summary(out)


@app.get("/audit")
async def get_audit(invoice_id: str | None = None, thread_id: str | None = None,
                    limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return audit.history(invoice_id=invoice_id, thread_id=thread_id, limit=limit)
