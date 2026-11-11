"""BM25 + expanded BM25 + character Dice, fused by weighted reciprocal rank."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from ..tools.document_reader import DocumentStore
from .original_tokenizer import char_ngrams, expand_query, tokenize


class OriginalHybridRetriever:
    def __init__(self, store: DocumentStore):
        self.chunks: list[dict[str, Any]] = []
        self.texts: list[str] = []
        for doc in store.documents.values():
            for section in doc.sections:
                self.chunks.append({"doc_id": doc.doc_id, "title": doc.title,
                                    "version_date": doc.version_date, "scope": doc.scope,
                                    "priority": doc.priority, "supersedes": doc.supersedes,
                                    "section_id": section.section_id, "snippet": section.content[:240]})
                if doc.doc_id in store.source_warnings:
                    self.chunks[-1]["source_warning"] = store.source_warnings[doc.doc_id]
                self.texts.append(f"{doc.title} {section.title} {section.content}")
        self.terms = [Counter(tokenize(text)) for text in self.texts]
        self.lengths = [sum(terms.values()) for terms in self.terms]
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))
        self.df = Counter(term for terms in self.terms for term in terms)
        self.grams = [char_ngrams(text) for text in self.texts]

    def _bm25(self, query: str) -> list[float]:
        query_terms = set(tokenize(query))
        scores = []
        for terms, length in zip(self.terms, self.lengths):
            score = 0.0
            for token in query_terms & terms.keys():
                tf = terms[token]
                idf = math.log(1 + (len(self.terms) - self.df[token] + 0.5) / (self.df[token] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / self.average_length))
            scores.append(score)
        return scores

    def search(self, query: str, top_k: int = 3, *, baseline: bool = False) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        lexical = self._bm25(query)
        if baseline:
            fused = lexical
            expanded = [0.0] * len(lexical)
            similarities = [0.0] * len(lexical)
        else:
            enriched = expand_query(query)
            expanded = self._bm25(enriched)
            grams = char_ngrams(enriched)
            similarities = [2 * len(grams & other) / max(1, len(grams) + len(other))
                            if len(grams & other) >= 2 else 0.0 for other in self.grams]
            fused = [0.0] * len(lexical)
            for scores, weight, threshold in ((lexical, 0.2, 0.0), (expanded, 0.55, 0.0),
                                              (similarities, 0.25, 0.08)):
                ranking = sorted((i for i, score in enumerate(scores) if score > threshold),
                                 key=lambda i: (-scores[i], self.chunks[i]["doc_id"]))
                for rank, index in enumerate(ranking, 1):
                    fused[index] += weight / (60 + rank)
        ranking = sorted((i for i, score in enumerate(fused) if score > 0),
                         key=lambda i: (-fused[i], self.chunks[i]["doc_id"]))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for i in ranking:
            row = self.chunks[i]
            if row["doc_id"] in seen:
                continue
            seen.add(row["doc_id"])
            results.append({**row, "score": round(fused[i], 6),
                            "channel_scores": {"bm25": round(lexical[i], 4),
                                               "expanded_bm25": round(expanded[i], 4),
                                               "ngram_dice": round(similarities[i], 4)}})
            if len(results) >= top_k:
                break
        return results
