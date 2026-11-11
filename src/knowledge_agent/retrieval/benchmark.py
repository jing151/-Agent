"""Retrieval metrics from labels, including graded NDCG and conflict-set completeness."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..tools.document_reader import DocumentStore


def score_ranking(ids: list[str], case: dict[str, Any], k: int = 3) -> dict[str, Any]:
    ids = ids[:k]
    relevant = set(case["relevant_doc_ids"])
    hit = len(set(ids) & relevant)
    grades = case.get("grades", {doc_id: 1 for doc_id in relevant})
    def dcg(values: list[int]) -> float:
        return sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(values, 1))
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    return {"recall_at_3": hit / len(relevant) if relevant else None,
            "precision_at_3": hit / k,
            "returned_precision_at_3": hit / len(ids) if ids else 0.0,
            "mrr": next((1 / rank for rank, doc_id in enumerate(ids, 1) if doc_id in relevant), 0.0) if relevant else None,
            "ndcg_at_3": dcg([grades.get(doc_id, 0) for doc_id in ids]) / ideal if ideal else None,
            "all_expected_recalled": set(case["expected_doc_ids"]) <= set(ids) if relevant else not ids,
            "irrelevant_returned": len(set(ids) & set(case["irrelevant_doc_ids"])),
            "returned": len(ids), "relevant_returned": hit}


def benchmark(store: DocumentStore, cases: list[dict[str, Any]], retriever: Any) -> dict[str, Any]:
    rows = []
    for case in cases:
        relevant, irrelevant = set(case["relevant_doc_ids"]), set(case["irrelevant_doc_ids"])
        if relevant & irrelevant or not (relevant | irrelevant) <= store.documents.keys():
            raise ValueError("Invalid relevance labels")
        query = " ".join(case.get("history", []) + [case["query"]])
        hits = retriever.search(query, 3)
        ids = [hit["doc_id"] for hit in hits]
        rows.append({**case, "effective_query": query, "ranking": ids, "hits": hits, **score_ranking(ids, case)})
    positive = [row for row in rows if row["relevant_doc_ids"]]
    negative = [row for row in rows if not row["relevant_doc_ids"]]
    conflicts = [row for row in positive if row.get("conflict")]
    result = {key: sum(row[key] for row in positive) / len(positive)
              for key in ["recall_at_3", "precision_at_3", "returned_precision_at_3", "mrr", "ndcg_at_3"]}
    result.update({"micro_returned_precision": sum(row["relevant_returned"] for row in rows) / max(1, sum(row["returned"] for row in rows)),
                   "conflict_all_recalled": sum(row["all_expected_recalled"] for row in conflicts) / len(conflicts),
                   "negative_false_positive_queries": sum(bool(row["ranking"]) for row in negative),
                   "positive_queries": len(positive), "negative_queries": len(negative), "cases": rows,
                   "definitions": {"precision_at_3": "relevant hits / 3, including unfilled slots; macro over positive queries",
                                   "returned_precision_at_3": "relevant hits / actual returned hits; positive queries only; empty=0",
                                   "ndcg_at_3": "graded gain=2^grade-1, log2 discount; positive queries only",
                                   "context_queries": "Benchmark concatenates prior USER question and current query; live model history tested separately"}})
    return result
