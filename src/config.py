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

# Paths (DB_PATH can be overridden, for example inside Docker)
GENERATED_DIR = ROOT / "data" / "generated"
INVOICE_DIR = GENERATED_DIR / "invoices"
CONTRACT_DIR = GENERATED_DIR / "contracts"
DB_PATH = Path(os.getenv("DB_PATH", str(GENERATED_DIR / "erp_simulated.db")))
ANSWER_KEY = ROOT / "eval" / "answer_key.json"
RESULTS_DIR = ROOT / "results"
