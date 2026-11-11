"""Reproducible retrieval benchmark; no model API is called."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .retrieval.retriever import HybridRetriever
from .tools.document_reader import DocumentStore


def evaluate_retrieval(kb_path: str | Path, eval_path: str | Path) -> dict[str, Any]:
    store = DocumentStore(kb_path)
    retriever = HybridRetriever(store)
    cases = json.loads(Path(eval_path).read_text(encoding="utf-8-sig"))
    report: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat(), "top_k": 3,
                              "knowledge_base_sha256": store.sha256, "case_count": len(cases),
                              "definition": "Recall@3=mean(|relevant ∩ top3|/|relevant|); MRR=mean(1/first_relevant_rank), rank within top3", "systems": {}}
    for name, baseline in (("baseline_bm25", True), ("hybrid", False)):
        rows = []
        for case in cases:
            ids = [hit["doc_id"] for hit in retriever.search(case["query"], 3, baseline=baseline)]
            relevant = set(case["relevant_doc_ids"])
            if not relevant or not relevant <= store.documents.keys():
                raise ValueError("Every evaluation case must have existing relevant documents")
            reciprocal = next((1 / rank for rank, doc_id in enumerate(ids, 1) if doc_id in relevant), 0.0)
            rows.append({**case, "retrieved_doc_ids": ids, "recall_at_3": len(set(ids) & relevant) / len(relevant),
                         "reciprocal_rank": reciprocal})
        report["systems"][name] = {"recall_at_3": sum(r["recall_at_3"] for r in rows) / len(rows),
                                     "mrr": sum(r["reciprocal_rank"] for r in rows) / len(rows), "cases": rows}
    return report
