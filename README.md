# Invoice Check Agents

A multi-agent system that checks supplier invoices against purchase orders, deliveries and the prices agreed in supplier contracts, based on accounts payable work I did at ALTRAD SPAR. A LangGraph supervisor routes each invoice through four specialist agents; plain Python rules make the money decisions; flagged invoices pause until a person approves or rejects them. Everything runs on a laptop with a free local model.

**All suppliers, invoices, purchase orders, deliveries and contracts in this project are SIMULATED.** The company names are invented and every document is stamped "SIMULATED DATA".

**Results dashboard:** https://ahmaduzzam31.github.io/invoice-check-agents/

![The app checking an invoice with an overcharge and hidden instructions](docs/img/app_flagged.png)

## The problem

Accounts payable teams check every supplier invoice before paying it:
- Is there a valid purchase order?
- Were the goods delivered in that quantity?
- Is the price the one in the contract?
- Have we already paid this invoice?

Doing this by hand is slow, and the costly mistakes (duplicates, invoices with no purchase order) are easy to miss in a busy month. This project automates the routine checks, lets clean invoices through, and holds problem invoices for a person, with a short note explaining why.

## Data (simulated)

`scripts/generate_data.py` creates everything with a fixed seed, so the same files come out on every run (`results/data_summary.json`):

| Item | Count |
|---|---|
| Suppliers, each with a contract PDF (price schedule, payment terms, invoicing rules) | 12 |
| Invoice PDFs, in 3 different layouts | 60 |
| Invoices with planted problems | 20 |
| Clean invoices (8 of them tricky: they look suspicious but are fine) | 40 |
| Money at risk in the planted problems (net) | EUR 45,738.22 |
| Purchase orders / order lines / deliveries / earlier paid invoices in SQLite | 96 / 251 / 253 / 43 |

The planted problems are:
- price above contract;
- quantity above delivery;
- duplicates (2 with the hyphen removed from the invoice number);
- missing or unknown purchase order;
- totals or VAT that do not add up;
- 2 invoices with hidden text telling an AI to approve them, which also carry a real overcharge.

The answer key with every true value is in `eval/answer_key.json`.

A separate **development set** (seed 7) was used to tune every prompt. The 60 test invoices were never seen by the models until the final evaluation.

## Architecture

```mermaid
flowchart TD
    PDF[Invoice PDF] --> SUP{Supervisor agent<br/>chooses the next step}
    SUP --> EX[Extractor agent<br/>Pydantic form + self-check retry]
    SUP --> MA[Matcher agent<br/>calls SQL tools]
    SUP --> CA[Contract agent<br/>reads agreed prices]
    SUP --> RV[Reviewer<br/>plain Python rules decide,<br/>model writes the note]
    EX --> SUP
    MA --> SUP
    CA --> SUP
    RV --> SUP
    MA <-->|read-only tools| MCP[MCP server] <--> DB[(SQLite ERP<br/>POs, deliveries,<br/>earlier invoices)]
    CA <-->|search filtered by supplier| PC[(Pinecone<br/>contract sections)]
    SUP -->|approved| OK[Approved automatically]
    SUP -->|flagged| HU[Pause for a person<br/>approve or reject]
    HU --> AUD[(Audit log)]
    OK --> AUD
    API[FastAPI] --> SUP
    UI[Streamlit app] --> API
```

| Part | Tool | Its one job |
|---|---|---|
| Chat model | `qwen2.5:7b` through Ollama | read invoices, choose steps, call tools, read contracts, write notes |
| Embeddings | `nomic-embed-text` through Ollama | turn contract sections into vectors |
| Agents | LangGraph | supervisor, four agents, and the pause for a person (interrupt + SQLite checkpoint) |
| Structured output | Pydantic | the fixed invoice form the extractor must fill in |
| Database tools | MCP server (`mcp`) + `langchain-mcp-adapters` | the agents' only, read-only way into the database |
| Contracts | Pinecone (free Starter plan) | store contract sections and search them, filtered by supplier |
| Money checks | plain Python (`src/rules.py`) | exact, auditable price, quantity, duplicate, PO and VAT checks |
| Service | FastAPI | check invoices (or uploaded PDFs), take decisions, show the audit log |
| App | Streamlit | run the agents, show every step and check, approve or reject |
| Packaging | Docker and Docker Compose | run the API and the app with one command |
| Note scoring | TruLens | trace the exception notes and score how well the evidence supports them |
| Tests | pytest + GitHub Actions | 95 tests on every push (`results/test_run.txt`) |

**Key design choice:** the model gathers and explains, and the rules decide. A small model is unreliable at arithmetic, and money checks must be exact, so price, quantity, duplicate, PO and VAT checks are plain Python. With perfect inputs, the rules find exactly the planted problems on all 60 invoices (`tests/test_rules.py`). A check that cannot be done (for example a missing contract price) holds the invoice for a person; it never approves it.

**Guard rails around each model step:**
- **Supervisor:** code corrects any choice that skips needed work, and there is a step limit.
- **Extractor:** a self-check confirms that the copied invoice and PO numbers really appear in the PDF, with one retry if they do not.
- **Matcher:** code checks that every needed lookup was made with the invoice's own values, and makes any missing call itself.
- **Contract agent:** every price it copies must appear in the contract text.
- **Prompt injection:** the invoice is treated as data, and any instructions in it are reported, never followed.

## How to run

You need Python 3.12, [Ollama](https://ollama.com) and a free [Pinecone](https://www.pinecone.io) account. It was built on a MacBook Air M5 (16 GB).

```bash
git clone https://github.com/ahmaduzzam31/invoice-check-agents.git
```

```bash
cd invoice-check-agents && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
ollama pull qwen2.5:7b && ollama pull nomic-embed-text
```

```bash
cp .env.example .env
```

Put your own Pinecone key in `.env` (`PINECONE_API_KEY=`). Then build the data and the contract index:

```bash
.venv/bin/python scripts/generate_data.py
```

```bash
.venv/bin/python scripts/index_contracts.py
```

Run the API and the app (two terminals):

```bash
.venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8000
```

```bash
.venv/bin/streamlit run app.py
```

Or run both in Docker (Ollama stays on the host; the key is passed in from `.env` at run time, never built into the image):

```bash
docker compose up --build
```

Check one invoice from the terminal:

```bash
.venv/bin/python scripts/run_invoice.py INV006
```

Repeat the evaluation, then the note scoring, the business analysis and the dashboard data:

```bash
.venv/bin/python scripts/evaluate.py
```

```bash
.venv/bin/python scripts/trulens_eval.py && .venv/bin/python scripts/business_analysis.py && .venv/bin/python scripts/build_dashboard_data.py
```

To use a paid model instead, change `LLM_PROVIDER` and `LLM_MODEL` in `.env`, add that provider's key and install its LangChain package. No code changes are needed.

## Results

One run on the 60 test invoices, 2026-11-09, models as above (`results/summary.json`). The baseline is **one model call** that gets the invoice text, the same evidence the agents can reach (all the supplier's orders and deliveries, its earlier invoices, and its contract prices) and the same checks written in plain words. The table is copied from `results/results_table.md`, which `scripts/build_dashboard_data.py` writes from `results/summary.json`.

| Measure (60 test invoices: 20 with problems, 40 clean) | Agent system | Single-call baseline |
|---|---|---|
| Problem invoices flagged | 20 of 20 | 20 of 20 |
| Clean invoices flagged (false alarms) | 2 of 40 | 40 of 40 |
| Recall | 1.0 | 1.0 |
| Precision | 0.9091 | 0.3333 |
| F1 | 0.9524 | 0.5 |
| Decision accuracy | 0.9667 | 0.3333 |
| Invoices with exactly the right problem list | 59 of 60 | 4 of 60 |
| Money at risk caught (net) | EUR 45,738.22 of EUR 45,738.22 | EUR 45,738.22 of EUR 45,738.22 |
| Problem invoices with the exact money at risk | 20 of 20 | 3 of 20 |
| Approved with no person needed | 38 (38 correctly) | 0 (0 correctly) |
| Mean seconds per invoice | 43.05 | 14.0 |
| Median seconds per invoice | 40.49 | 11.89 |

**What this shows:**
- **The agent system caught every problem invoice and let 38 clean invoices through on its own.** Every flagged problem invoice had the exact money at risk, because the rules, not the model, do the arithmetic.
- **The baseline "caught" every problem only by flagging every invoice.** It found 0 of 4 duplicates and named problems that were not there (for example "totals or VAT wrong" on 36 invoices without that problem). A system that flags everything saves no work.
- **The agent system's mistakes were safe ones:**
  - On 2 clean invoices (INV041, INV055), the contract agent did not copy one item's price from the contract. The rules held the invoice for a person instead of approving it blind.
  - On 1 duplicate (INV048), the extractor wrongly reported the normal payment-terms line as "hidden instructions". The flag was still correct.
- **It is slower:** 43.05 s against 14.0 s per invoice on average, because one invoice takes several model calls.

**Inside the agent system** (`results/summary.json`, `agent_details`):
- **Extraction:** all 9 fields correct on 60 of 60 invoices, with the self-check retry used 4 times.
- **Supervisor:** its choice needed no correction in 300 of 300 routing steps. The agents' dependencies fix the order, so this step is easy, and most of the reliability comes from the code around it.
- **Matcher:** made every lookup itself on 58 of 60 invoices. Code filled in the duplicate lookup on 2.
- **Contract search:** filtering by supplier found the right price 60 of 60 times and the right payment terms 12 of 12. Without the filter, the payment terms were right only 1 of 12 times, because every contract uses the same wording (`results/contract_search_check.json`).

**Exception notes, scored with TruLens 2.16.0** (`results/trulens_summary.json`, `results/trulens_notes.csv`):
- **Groundedness (local model as judge):** 0.7841 on average across the 22 notes. 5 notes were fully grounded.
- **Coverage (plain check):** 22 of 22 notes mention every problem found.

Reading the lower-scored notes, most lost points on advice sentences (for example "ask the supplier for a credit note"), which the evidence cannot contain. Two notes have real faults:
- INV048 advises a credit note for a duplicate, which should be rejected, and repeats the false "instructions" claim.
- INV055 says the invoice "does not contain any issues" while asking for a price check.

**Business view** (saved SQL in `sql/`, results in `results/business_analysis.json`):
- **Duplicates were 4 problem invoices but EUR 33,389.50 of the EUR 45,738.22 at risk.** Missing purchase orders added EUR 10,707.38. Price and quantity overcharges were frequent but small: EUR 982.33 and EUR 346.94.
- **Approved automatically:** 38 invoices worth EUR 207,926.42 gross.
- **Rightly held for a person:** 20 problem invoices holding all EUR 45,738.22 at risk.
- **False alarms:** 2 clean invoices worth EUR 49,655.10 gross were held unnecessarily.
- **Riskiest supplier:** Boyne Industrial Paints, with EUR 22,144.84 at risk.

## Screenshots

| Agent run on a flagged invoice | Results dashboard |
|---|---|
| ![App](docs/img/app_flagged.png) | ![Dashboard](docs/img/dashboard.png) |

## Limitations

- **Simulated data.** Real invoices are messier: scans, handwriting, credit notes, partial payments, currencies and price changes during a contract.
- **Small test set and a single run:** 60 invoices.
- **The same small model writes the notes and judges them in TruLens,** so the groundedness scores are rough, and the judge marks down useful advice.
- **The single-call baseline is sensitive to its prompt.** On the dev set, one version approved every invoice and the final version flagged every invoice. I stopped after two fair attempts rather than tune it towards either result.
- **The contract agent occasionally leaves out an item's price** (2 invoices). The rules make this safe, but it costs a person's time.
- **The extractor once mistook payment terms for hidden instructions.** A small model finds this distinction hard.
- **The supervisor adds little here,** because the work has a fixed order. A plain fixed pipeline would give the same decisions, slightly faster.
- **Duplicate detection only compares with invoices in the database,** not with other invoices in the same batch.
- **About 43 seconds per invoice on a laptop** is fine for a demo, slow for thousands of invoices a day.

## Next steps

- Make the contract agent return every requested item, or read the price table with plain code, then re-test on new invoices.
- Separate payment terms from instructions with a stricter injection check, also tested on new invoices.
- Replace the supervisor with a fixed pipeline and compare speed and results, keeping the supervisor only where the path really varies (for example credit notes or missing documents).
- Judge the notes with a stronger model and a blind human check.
- Add credit notes, partial deliveries across many invoices, and price changes during a contract.
- Run the evaluation with a hosted model to compare cost, speed and accuracy.

## Repo layout

```
app.py                  Streamlit app (talks to the API)
src/                    agents, graph, extractor, rules, SQL tools, contracts, baseline, API, audit log
mcp_server/server.py    read-only MCP server for the SQL tools
scripts/                data generation, contract indexing, evaluation, TruLens, business analysis, dashboard data
sql/                    the saved business analysis queries
eval/answer_key.json    the truth for every simulated test invoice
results/                every number in this README
docs/                   GitHub Pages dashboard and screenshots
tests/                  95 tests (SQL tools, MCP server, rules, graph routing, API)
Dockerfile, docker-compose.yml
INTERVIEW_NOTES.md      likely interview questions and answers
```
