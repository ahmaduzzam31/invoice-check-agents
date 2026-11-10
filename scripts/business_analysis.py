"""Business view of the evaluation: run the saved SQL queries in sql/ and save their results.

Loads the answer key, the agent system's results and the supplier table into an in-memory SQLite
database, runs every sql/*.sql file in order, and writes results/business_analysis.json with the
query text and its rows. All data is SIMULATED.

Run after scripts/evaluate.py:  .venv/bin/python scripts/business_analysis.py
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

SQL_DIR = config.ROOT / "sql"


def main() -> None:
    invoices = json.loads(config.ANSWER_KEY.read_text())["invoices"]
    runs = {r["invoice_id"]: r for r in (json.loads(l) for l in
                                          (config.RESULTS_DIR / "runs_agent.jsonl").read_text().splitlines())}
    con = sqlite3.connect(":memory:")
    con.execute(f"ATTACH DATABASE 'file:{config.DB_PATH}?mode=ro' AS erp")
    con.executescript("""
        CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY, supplier_id TEXT, scenario TEXT, has_problem INTEGER,
                               net_total REAL, gross_total REAL, money_at_risk_net REAL);
        CREATE TABLE main_problem (invoice_id TEXT PRIMARY KEY, main_problem TEXT);
        CREATE TABLE agent_results (invoice_id TEXT PRIMARY KEY, decision TEXT, problems TEXT, money_at_risk_net REAL);
    """)
    for i in invoices:
        con.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
                    (i["invoice_id"], i["supplier_id"], i["scenario"], int(bool(i["problems"])),
                     i["correct_net_total"], i["gross_total"], i["money_at_risk_net"]))
        main = [p for p in i["problems"] if p != "prompt_injection"]
        if main:
            con.execute("INSERT INTO main_problem VALUES (?,?)", (i["invoice_id"], main[0]))
        r = runs[i["invoice_id"]]
        con.execute("INSERT INTO agent_results VALUES (?,?,?,?)",
                    (i["invoice_id"], r["decision"], ";".join(r["problems"]), r["money_at_risk_net"]))

    out = {"note": "SIMULATED data. Each query is in sql/; rows are exactly what the query returned.", "queries": []}
    for path in sorted(SQL_DIR.glob("*.sql")):
        sql = path.read_text()
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        out["queries"].append({"file": f"sql/{path.name}", "sql": sql, "rows": rows})
        print(f"\n== {path.name}")
        for row in rows:
            print("  ", row)
    (config.RESULTS_DIR / "business_analysis.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
