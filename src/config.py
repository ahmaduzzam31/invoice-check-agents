"""All settings in one place, read from .env (see .env.example)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Models (swap provider by changing .env only)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "8192"))
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "ollama")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Pinecone (free Starter plan). The key only ever lives in .env.
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "invoice-contracts")

# Which simulated data set to use: "test" (seed 42, the 60 evaluation invoices) or
# "dev" (seed 7, used only to develop and tune prompts). One setting switches the database,
# the invoices, the answer key and the Pinecone namespace together.
DATA_SET = os.getenv("DATA_SET", "test")
if DATA_SET == "dev":
    GENERATED_DIR = ROOT / "data" / "dev"
    ANSWER_KEY = GENERATED_DIR / "answer_key.json"
    CONTRACT_NAMESPACE = "contracts-dev"
else:
    GENERATED_DIR = ROOT / "data" / "generated"
    ANSWER_KEY = ROOT / "eval" / "answer_key.json"
    CONTRACT_NAMESPACE = "contracts"

# Paths (DB_PATH can be overridden, for example inside Docker)
INVOICE_DIR = GENERATED_DIR / "invoices"
CONTRACT_DIR = GENERATED_DIR / "contracts"
DB_PATH = Path(os.getenv("DB_PATH", str(GENERATED_DIR / "erp_simulated.db")))
CHECKPOINT_DB = Path(os.getenv("CHECKPOINT_DB", str(ROOT / "data" / "checkpoints.db")))  # paused runs resume
AUDIT_DB = Path(os.getenv("AUDIT_DB", str(ROOT / "data" / "audit.db")))  # who decided what, when
UPLOAD_DIR = ROOT / "data" / "uploads"  # PDFs sent to the API
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
RESULTS_DIR = ROOT / "results"
