"""Split the 12 simulated contracts into sections and store them in Pinecone (free Starter plan).

Safe to re-run: every section has a fixed id, so re-running overwrites instead of duplicating.
Run:  .venv/bin/python scripts/index_contracts.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pinecone import Pinecone, ServerlessSpec  # noqa: E402

from src import config  # noqa: E402
from src.contracts import DIMENSION, NAMESPACE, get_store, split_contract  # noqa: E402


def main() -> None:
    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    if config.PINECONE_INDEX not in [i.name for i in pc.list_indexes()]:
        print(f"Creating index {config.PINECONE_INDEX} ({DIMENSION} dimensions, cosine, aws us-east-1)")
        pc.create_index(name=config.PINECONE_INDEX, dimension=DIMENSION, metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region="us-east-1"))
    while not pc.describe_index(config.PINECONE_INDEX).status["ready"]:
        time.sleep(2)

    docs = []
    for path in sorted(config.CONTRACT_DIR.glob("contract_*.pdf")):
        docs.extend(split_contract(path))
    # Fixed ids, numbered within each contract, so a re-run overwrites the same vectors.
    ids, seen = [], {}
    for d in docs:
        n = seen.get(d.metadata["contract_id"], 0)
        seen[d.metadata["contract_id"]] = n + 1
        ids.append(f"{d.metadata['contract_id']}#{n:02d}")

    t0 = time.perf_counter()
    get_store().add_documents(docs, ids=ids)
    seconds = round(time.perf_counter() - t0, 2)

    index = pc.Index(config.PINECONE_INDEX)
    count = 0
    for _ in range(30):  # Pinecone counts update a few seconds after writing
        ns = index.describe_index_stats().namespaces.get(NAMESPACE)
        count = ns.vector_count if ns else 0
        if count >= len(docs):
            break
        time.sleep(2)

    stats = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "index": config.PINECONE_INDEX, "namespace": NAMESPACE, "embedding_model": config.EMBED_MODEL,
             "dimension": DIMENSION, "contracts": len(seen), "sections": len(docs),
             "vectors_in_namespace": count, "embed_and_upload_seconds": seconds}
    config.RESULTS_DIR.mkdir(exist_ok=True)
    (config.RESULTS_DIR / "contract_index_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
