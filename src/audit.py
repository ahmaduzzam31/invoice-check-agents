"""Append-only audit log: every automatic check and every human decision, with its evidence.

Finance controls need to show who decided what, when, and why. Rows are only ever added,
never changed or deleted, so the history cannot be rewritten through this module.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from src import config

SCHEMA = """CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, thread_id TEXT NOT NULL, invoice_id TEXT,
    event TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT, final_status TEXT, problems TEXT,
    money_at_risk_net REAL, note TEXT, checks TEXT)"""


def _connect() -> sqlite3.Connection:
    config.AUDIT_DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(config.AUDIT_DB)
    con.execute(SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_check(out: dict) -> None:
    """Log the automatic result of one agent run (actor = the system)."""
    r = out.get("result", {})
    with closing(_connect()) as con, con:
        con.execute("INSERT INTO audit_log (ts_utc, thread_id, invoice_id, event, actor, decision, final_status, "
                    "problems, money_at_risk_net, note, checks) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (_now(), out["thread_id"], out.get("invoice_id"), "checked", "system (agents and rules)",
                     r.get("decision"), out.get("final_status"), json.dumps(r.get("problems", [])),
                     r.get("money_at_risk_net"), out.get("note"), json.dumps(r.get("checks", []))))


def record_decision(out: dict) -> None:
    """Log a person's approve or reject decision on a flagged invoice."""
    h = out.get("human", {})
    with closing(_connect()) as con, con:
        con.execute("INSERT INTO audit_log (ts_utc, thread_id, invoice_id, event, actor, decision, final_status, "
                    "problems, money_at_risk_net, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_now(), out["thread_id"], out.get("invoice_id"), "human_decision", h.get("by", "unknown"),
                     h.get("decision"), out.get("final_status"), json.dumps(out.get("result", {}).get("problems", [])),
                     out.get("result", {}).get("money_at_risk_net"), h.get("comment")))


def history(invoice_id: str | None = None, thread_id: str | None = None, limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM audit_log", []
    if invoice_id:
        sql, args = sql + " WHERE invoice_id = ?", [invoice_id]
    elif thread_id:
        sql, args = sql + " WHERE thread_id = ?", [thread_id]
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit])]
    for r in rows:
        for k in ("problems", "checks"):
            r[k] = json.loads(r[k]) if r.get(k) else None
    return rows
