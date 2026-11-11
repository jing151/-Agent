"""Local multi-route retrieval without modifying the three frozen baselines.

The optional field route is an experiment. Scores are relevance signals, not
claims, policy precedence, or model decisions. Source snippets stay verbatim.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any

from ..tools.document_reader import DocumentStore
from .normalization import missing_attributes, plan_query, source_topics
from .retriever import HybridRetriever, meaningful


class GeneralizingRetriever:
    def __init__(self, store: DocumentStore, *, normalization: bool = True,
                 fields: bool = False, filters: bool = True):
        self.store = store
        self.base = HybridRetriever(store)
        self.normalization, self.fields, self.filters = normalization, fields, filters
        self.source_text = {doc.doc_id: f"{doc.title} {doc.scope or ''} {doc.content}"
                            for doc in store.documents.values()}
        self.topics = {doc_id: source_topics(text) for doc_id, text in self.source_text.items()}
        self.available_topics = set().union(*self.topics.values())
        # Scope is indexed only here; dates, IDs, priority and supersedes are
        # never relevance features. Sections retain current source boundaries.
        self.field_terms = [Counter(meaningful(f"{store.documents[row['doc_id']].title} "
                           f"{store.documents[row['doc_id']].scope or ''} "
                           f"{next(s.title for s in store.documents[row['doc_id']].sections if s.section_id == row['section_id'])}"))
                            for row in self.base.chunks]
        self.field_df = Counter(term for terms in self.field_terms for term in terms)
        self.field_average = sum(sum(terms.values()) for terms in self.field_terms) / max(1, len(self.field_terms))

    def _field_search(self, query: str) -> list[dict[str, Any]]:
        scores = []
        query_terms = meaningful(query)
        for index, terms in enumerate(self.field_terms):
            score = 0.0
            for term in query_terms & terms.keys():
                tf = terms[term]
                idf = math.log(1 + (len(self.field_terms) - self.field_df[term] + .5) / (self.field_df[term] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * sum(terms.values()) / max(1, self.field_average)))
            if score > 0:
                scores.append({**self.base.chunks[index], "score": score})
        return sorted(scores, key=lambda row: (-row["score"], row["doc_id"], row["section_id"]))

    def explain(self, query: str, top_k: int = 3) -> dict[str, Any]:
        plan = plan_query(query, self.available_topics)
        routes = [{"name": "original", "query": query, "weight": .35,
                   "hits": self.base.search(query, max(1, len(self.store.documents)))}]
        if self.normalization:
            for number, canonical in enumerate(plan.canonical_queries):
                routes.append({"name": f"concept_{number}", "query": canonical, "weight": .65,
                               "hits": self.base.search(canonical, max(1, len(self.store.documents)))})
        if self.fields:
            field_query = query + (" " + " ".join(plan.canonical_queries) if self.normalization else "")
            routes.append({"name": "fields", "query": field_query, "weight": .15, "hits": self._field_search(field_query)})
        candidates: dict[str, dict[str, Any]] = {}
        for route in routes:
            peak = max((hit["score"] for hit in route["hits"]), default=0)
            # Best chunk per document per route; extra sections must not create
            # an artificial document vote count.
            seen = set()
            for hit in route["hits"]:
                doc_id = hit["doc_id"]
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                value = route["weight"] * hit["score"] / peak if peak else 0
                candidate = candidates.setdefault(doc_id, {"hit": hit, "score": 0.0, "routes": {}, "best_chunk_score": -1.0})
                candidate["score"] += value
                candidate["routes"][route["name"]] = round(value, 8)
                if value > candidate["best_chunk_score"]:
                    candidate["hit"], candidate["best_chunk_score"] = hit, value
        eligible = []
        decisions = []
        for doc_id, candidate in candidates.items():
            reasons = []
            if self.filters:
                if plan.topics and not self.topics[doc_id] & set(plan.topics):
                    reasons.append("business_object_mismatch")
                reasons += [f"missing_attribute:{name}" for name in missing_attributes(plan.attributes, self.source_text[doc_id])]
            decisions.append({"doc_id": doc_id, "score": round(candidate["score"], 8),
                              "source_topics": sorted(self.topics[doc_id]), "routes": candidate["routes"],
                              "rejected_by": reasons})
            if not reasons:
                eligible.append(candidate)
        peak = max((item["score"] for item in eligible), default=0)
        # Same relative cutoff as C, applied after object/attribute checks.
        eligible = [item for item in eligible if item["score"] >= .30 * peak and item["score"] > 0]
        eligible.sort(key=lambda item: (-item["score"], item["hit"]["doc_id"]))
        results = [{**item["hit"], "score": round(item["score"], 6),
                    "channel_scores": {**item["hit"].get("channel_scores", {}),
                                       "route_support": item["routes"]}} for item in eligible[:max(0, top_k)]]
        return {"plan": plan.as_dict(), "routes": routes,
                "route_candidate_doc_ids": sorted(candidates),
                "candidate_doc_ids": [item["hit"]["doc_id"] for item in eligible],
                "eligible_doc_ids": [item["hit"]["doc_id"] for item in eligible],
                "candidate_decisions": sorted(decisions, key=lambda item: (-item["score"], item["doc_id"])),
                "results": results}

    def search(self, query: str, top_k: int = 3, *, baseline: bool = False) -> list[dict[str, Any]]:
        if baseline:
            return self.base.search(query, top_k, baseline=True)
        if not query.strip() or top_k < 1:
            return []
        return self.explain(query, top_k)["results"]
