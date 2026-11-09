"""Split supplier contracts into sections and search them in Pinecone.

Each section is stored with its supplier_id, so a search can be limited to one supplier's
contract. That matters here: all contracts share the same wording, only names and prices differ.
"""

import re
from functools import lru_cache

import pymupdf
from langchain_core.documents import Document

from src import config, sql_tools
from src.models import get_embeddings

NAMESPACE = "contracts"
DIMENSION = 768  # nomic-embed-text
NOISE = "SIMULATED DATA - NOT A REAL DOCUMENT - CREATED FOR A PORTFOLIO PROJECT"
HEADING = re.compile(r"^(\d+\. .+|Schedule 1: .+)$")


def _schedule_rows(lines: list[str]) -> list[str]:
    """The PDF table comes out one cell per line; rebuild it as 'code | description | unit | price' rows."""
    try:
        start = lines.index("Agreed unit price") + 1
    except ValueError:
        return lines
    cells = [c for c in lines[start:] if c]
    rows = [" | ".join(cells[i:i + 4]) for i in range(0, len(cells) - 3, 4)]
    return ["Item code | Description | Unit | Agreed unit price (EUR, excl. VAT)"] + rows


def split_contract(path) -> list[Document]:
    text = "\n".join(page.get_text() for page in pymupdf.open(path))
    lines = [l.strip() for l in text.splitlines() if l.strip() and NOISE not in l]
    contract_id = re.search(r"Supply Agreement (CTR-\d{4}-\d{2})", text).group(1)
    vat = re.search(r"VAT No (\S+?)\.", text).group(1)
    sup = sql_tools.find_supplier(vat_number=vat)["suppliers"][0]

    sections, current, body = [], "Parties", []
    for line in lines:
        if HEADING.match(line):
            sections.append((current, body))
            current, body = line, []
        elif not line.startswith("Supply Agreement"):
            body.append(line)
    sections.append((current, body))

    docs = []
    for title, body in sections:
        if title.startswith("Schedule 1"):
            body = _schedule_rows(body)
        content = "\n".join(body)
        header = f"Contract {contract_id} with {sup['name']} ({sup['supplier_id']}). Section: {title}"
        docs.append(Document(page_content=f"{header}\n{content}",
                             metadata={"supplier_id": sup["supplier_id"], "supplier_name": sup["name"],
                                       "contract_id": contract_id, "section": title,
                                       "source": path.name}))
    return docs


@lru_cache(maxsize=1)
def get_store():
    from langchain_pinecone import PineconeVectorStore
    from pinecone import Pinecone

    if not config.PINECONE_API_KEY:
        raise RuntimeError("PINECONE_API_KEY is missing from .env")
    index = Pinecone(api_key=config.PINECONE_API_KEY).Index(config.PINECONE_INDEX)
    return PineconeVectorStore(index=index, embedding=get_embeddings(), namespace=NAMESPACE)


def search_contract(supplier_id: str | None, query: str, k: int = 3) -> list[dict]:
    """Return the k best contract sections for the query, only from this supplier's contract
    (or from all contracts if supplier_id is None)."""
    flt = {"supplier_id": {"$eq": supplier_id}} if supplier_id else None
    hits = get_store().similarity_search_with_score(query, k=k, filter=flt)
    return [{"text": d.page_content, "score": round(float(s), 4), **d.metadata} for d, s in hits]
