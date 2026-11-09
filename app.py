"""Streamlit app: check invoices, watch each agent's step, and approve or reject flagged invoices.

The app is only a screen: all work goes through the FastAPI service (src/api.py), like a real system.
Start the API first, then the app:
    .venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8000
    .venv/bin/streamlit run app.py
"""

import os

import httpx
import pandas as pd
import pymupdf
import streamlit as st

from src import config

API = os.getenv("API_URL", "http://127.0.0.1:8000")
AGENT_ICONS = {"supervisor": "🧭", "extractor": "📄", "matcher": "🔎", "contract_agent": "📑",
               "reviewer": "⚖️", "human_review": "🙋"}
STATUS_ICONS = {"pass": "✅", "fail": "❌", "unverified": "⚠️"}

st.set_page_config(page_title="Invoice Check Agents", page_icon="🧾", layout="wide")


def api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=300, **kw)
    except httpx.HTTPError as e:
        st.error(f"Cannot reach the API at {API}. Is it running? ({type(e).__name__})")
        st.stop()
    if r.status_code >= 400:
        st.error(f"API error {r.status_code}: {r.json().get('detail', r.text)}")
        return None
    return r.json()


@st.cache_data(show_spinner=False)
def pdf_image(path: str) -> bytes:
    with pymupdf.open(path) as doc:
        return doc[0].get_pixmap(dpi=80).tobytes("png")


def show_result(res: dict) -> None:
    money = res["money_at_risk_net"] or 0
    if res["final_status"] == "approved_automatically":
        st.success("✅ **Approved automatically**: every check passed.")
    elif res["final_status"] == "waiting_for_person":
        st.warning(f"⏸️ **Flagged and waiting for a person**. Money at risk: **EUR {money:,.2f}** (excl. VAT)")
    elif res["final_status"] == "approved_by_person":
        st.info(f"🙋 **Approved by {res['human']['by']}** after review.")
    elif res["final_status"] == "rejected_by_person":
        st.error(f"🙋 **Rejected by {res['human']['by']}**: {res['human'].get('comment') or 'no comment'}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Problems found", len(res["problems"]))
    c2.metric("Money at risk (EUR)", f"{money:,.2f}")
    c3.metric("Time", f"{res['seconds']} s" if res.get("seconds") else "n/a")
    st.markdown(f"**Reviewer's note:** {res['note']}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Checks (plain rules, not AI)")
        rows = [{"": STATUS_ICONS.get(c["status"], ""), "Check": c["check"].replace("_", " "),
                 "Result": c["status"], "Detail": c["detail"]} for c in res["checks"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.subheader("Agent steps")
        for i, s in enumerate(res["steps"], start=1):
            parts = s["detail"].split(" | ")
            with st.expander(f"{i}. {AGENT_ICONS.get(s['agent'], '•')} **{s['agent']}** ({s['seconds']} s): {parts[0][:90]}",
                             expanded=s["agent"] in ("matcher", "reviewer")):
                for p in parts:
                    st.markdown(f"- {p}")
    with right:
        st.subheader("What the extractor read")
        e = res.get("extraction")
        if e:
            st.markdown(f"**{e['supplier_name']}** ({e.get('supplier_vat_number') or 'no VAT no.'})  \n"
                        f"Invoice **{e['invoice_number']}**, {e['invoice_date']}, PO **{e['po_number'] or 'none'}**")
            st.dataframe(pd.DataFrame(e["lines"]), hide_index=True, use_container_width=True)
            st.markdown(f"Net {e['net_total']:,.2f} · VAT {e['vat_amount']:,.2f} · **Gross {e['gross_total']:,.2f}**")
            if e.get("instructions_found"):
                st.error(f"Hidden instructions found in the invoice (not followed): “{e['instructions_found']}”")
        else:
            st.error("The invoice could not be read.")


def decision_form(res: dict, key: str) -> None:
    st.subheader("Your decision")
    with st.form(f"decide-{key}"):
        by = st.text_input("Your name", max_chars=80)
        comment = st.text_input("Comment (optional)", max_chars=500)
        c1, c2 = st.columns(2)
        approve = c1.form_submit_button("Approve for payment", use_container_width=True)
        reject = c2.form_submit_button("Reject", type="primary", use_container_width=True)
    if approve or reject:
        if not by.strip():
            st.error("Please enter your name: every decision is recorded in the audit log.")
            return
        out = api("POST", f"/runs/{res['thread_id']}/decision",
                  json={"decision": "approve" if approve else "reject", "by": by.strip(), "comment": comment})
        if out:
            st.session_state.result = {**out, "seconds": res.get("seconds")}
            st.rerun()


# ---------------- sidebar ----------------
health = api("GET", "/health")
with st.sidebar:
    st.title("🧾 Invoice Check Agents")
    st.caption("A supervisor routes each invoice through four agents. Plain rules decide; "
               "flagged invoices wait for a person.")
    st.markdown(f"**Model:** `{health['model']}` (local, free)  \n**Data set:** {health['data_set']}  \n"
                f"**API:** {API}")
    st.warning("All suppliers, invoices, orders and contracts are **simulated**.")
    st.markdown("**Agents:** 🧭 supervisor · 📄 extractor · 🔎 matcher (MCP tools) · "
                "📑 contract agent (Pinecone) · ⚖️ reviewer · 🙋 you")

tab_check, tab_queue, tab_audit = st.tabs(["Check an invoice", "Waiting for a person", "Audit log"])

# ---------------- check an invoice ----------------
with tab_check:
    invoices = api("GET", "/invoices") or []
    src = st.radio("Invoice", ["Pick a simulated invoice", "Upload a PDF"], horizontal=True)
    chosen, upload = None, None
    if src == "Pick a simulated invoice":
        ids = [i["invoice_id"] for i in invoices]
        chosen = st.selectbox("Simulated invoice", ids, index=0 if ids else None)
    else:
        upload = st.file_uploader("Invoice PDF (up to 5 MB)", type=["pdf"])

    preview, run_col = st.columns([1, 5])
    if chosen:
        path = config.INVOICE_DIR / f"invoice_{chosen[3:]}.pdf"
        if path.exists():
            with preview.popover(f"Preview {chosen}"):
                st.image(pdf_image(str(path)), caption=f"{chosen} (simulated)", width=520)
    if run_col.button("Run the agents", type="primary", disabled=not (chosen or upload)):
        with st.spinner("The agents are working. This takes about 40 to 60 seconds on a laptop..."):
            if upload:
                res = api("POST", "/uploads/check", files={"file": (upload.name, upload.getvalue(), "application/pdf")})
            else:
                res = api("POST", f"/invoices/{chosen}/check")
        if res:
            st.session_state.result = res

    res = st.session_state.get("result")
    if res:
        st.divider()
        st.caption(f"Run {res['thread_id']}")
        show_result(res)
        if res["paused_for_person"]:
            decision_form(res, "main")

# ---------------- waiting for a person ----------------
with tab_queue:
    rows = api("GET", "/audit", params={"limit": 1000}) or []
    decided = {r["thread_id"] for r in rows if r["event"] == "human_decision"}
    waiting = [r for r in rows if r["event"] == "checked" and r["final_status"] == "waiting_for_person"
               and r["thread_id"] not in decided]
    st.markdown(f"**{len(waiting)}** flagged invoice(s) waiting for a decision.")
    for r in waiting:
        with st.expander(f"{r['invoice_id']}: {', '.join(r['problems']) or 'could not verify'}  ·  "
                         f"EUR {r['money_at_risk_net']:,.2f} at risk  ·  {r['ts_utc']}"):
            st.markdown(r["note"])
            run = api("GET", f"/runs/{r['thread_id']}")
            if run and run["paused_for_person"]:
                decision_form(run, r["thread_id"])

# ---------------- audit log ----------------
with tab_audit:
    rows = api("GET", "/audit", params={"limit": 1000}) or []
    if rows:
        df = pd.DataFrame(rows)[["ts_utc", "invoice_id", "event", "actor", "decision", "final_status",
                                 "problems", "money_at_risk_net", "note"]]
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.caption("Append-only: rows are added, never changed or deleted.")
    else:
        st.info("No checks recorded yet.")
