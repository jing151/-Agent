"""Same-corpus retrieval comparisons with complete conflict sets and explicit denominators."""
from __future__ import annotations

import math
from typing import Any


def evaluate(retriever: Any, cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        query = " ".join(case.get("history", []) + [case["query"]])
        hits = retriever.search(query, 5)
        ids = [hit["doc_id"] for hit in hits]
        relevant = set(case["relevant_doc_ids"])
        grades = case.get("relevance_grade", case.get("grades", {doc_id: 1 for doc_id in relevant}))
        conflict = case.get("expected_conflict_doc_ids", case.get("expected_doc_ids", []) if case.get("conflict") else [])
        def dcg(values):
            return sum((2 ** value - 1) / math.log2(rank + 1) for rank, value in enumerate(values, 1))
        ideal = dcg(sorted(grades.values(), reverse=True)[:3])
        first = next((rank for rank, doc_id in enumerate(ids, 1) if doc_id in relevant), None)
        if hasattr(retriever, "explain"):
            pool = retriever.explain(query, 5).get("candidate_doc_ids", ids)
        else:
            pool = [hit["doc_id"] for hit in retriever.search(query, 100)]
        row = {**case, "effective_query": query, "top_3": ids[:3], "top_5": ids, "hits": hits,
               "candidate_doc_ids": pool,
               "recall_at_3": len(set(ids[:3]) & relevant) / len(relevant) if relevant else None,
               "recall_at_5": len(set(ids) & relevant) / len(relevant) if relevant else None,
               "precision_at_3": len(set(ids[:3]) & relevant) / 3,
               "returned_precision_at_3": len(set(ids[:3]) & relevant) / len(ids[:3]) if ids else 0,
               "mrr": 1 / first if first else 0,
               "mrr_at_3": 1 / first if first and first <= 3 else 0,
               "ndcg_at_3": dcg([grades.get(doc_id, 0) for doc_id in ids[:3]]) / ideal if ideal else None,
               "conflict_complete_at_3": set(conflict) <= set(ids[:3]) if conflict else None,
               "conflict_complete_at_5": set(conflict) <= set(ids) if conflict else None,
               "conflict_complete_in_pool": set(conflict) <= set(pool) if conflict else None}
        rows.append(row)
    positive = [row for row in rows if row["relevant_doc_ids"]]
    negative = [row for row in rows if not row["relevant_doc_ids"]]
    conflicts = [row for row in rows if row["conflict_complete_at_3"] is not None]
    metrics = {key: sum(row[key] for row in positive) / len(positive) if positive else None for key in [
        "recall_at_3", "recall_at_5", "precision_at_3", "mrr", "mrr_at_3", "ndcg_at_3", "returned_precision_at_3"]}
    metrics.update({key: sum(row[key] for row in conflicts) / len(conflicts) if conflicts else None for key in [
        "conflict_complete_at_3", "conflict_complete_at_5", "conflict_complete_in_pool"]})
    metrics.update({"micro_returned_precision_at_3": sum(len(set(row["top_3"]) & set(row["relevant_doc_ids"])) for row in rows)
                    / max(1, sum(len(row["top_3"]) for row in rows)),
                    "negative_false_positive_rate": sum(bool(row["top_3"]) for row in negative) / len(negative) if negative else None,
                    "positive_queries": len(positive), "negative_queries": len(negative), "conflict_queries": len(conflicts)})
    return {"metrics": metrics, "cases": rows,
            "definitions": {"positive_macro": "Recall/P@3/MRR/NDCG/returned precision averaged over positive queries",
                "precision_at_3": "relevant in first 3 / 3; unfilled slots remain in denominator",
                "mrr": "first relevant rank within top5; mrr_at_3 reported separately",
                "ndcg_at_3": "gain=2^grade-1; log2 rank discount",
                "micro_returned_precision_at_3": "all relevant top3 hits / all returned top3 hits, including negative queries",
                "negative_false_positive_rate": "negative queries with any top3 result / all negative queries",
                "conflict": "all labeled conflict documents must be present; no first-hit success",
                "candidate_pool": "All positive eligible scores after filters, before top-k; route union is recorded separately by explain()",
                "history": "Evaluator joins prior USER query for retrieval only; no model conversation claim"}}
