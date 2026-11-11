"""Hybrid retrieval with magnitude-aware reranking and source-independent specificity filters."""
from __future__ import annotations

import math
from typing import Any

from ..tools.document_reader import DocumentStore
from .original import OriginalHybridRetriever
from .tokenizer import SYNONYM_GROUPS, char_ngrams, expand_query, tokenize

# Generic workflow terms must not outrank the actual topic or a specific condition.
GENERIC = set("制度 流程 规则 渠道 方式 其他 具体 日期 工单 门户 步骤 查询 今年 faq it".split())


def meaningful(text: str) -> set[str]:
    return {word for word in tokenize(text) if len(word) >= 2 and not word.isdigit() and word not in GENERIC}


class HybridRetriever(OriginalHybridRetriever):
    def __init__(self, store: DocumentStore):
        super().__init__(store)
        self.store = store
        self.alias_terms = {word for group in SYNONYM_GROUPS for alias in group for word in meaningful(alias)}

    def _bm25(self, query: str) -> list[float]:
        query_terms = meaningful(query)
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
        if baseline:
            return OriginalHybridRetriever(self.store).search(query, top_k, baseline=True)
        if not query.strip() or top_k < 1:
            return []
        lexical, expanded = self._bm25(query), self._bm25(expand_query(query))
        grams = char_ngrams(expand_query(query))
        similarities = [2 * len(grams & other) / max(1, len(grams) + len(other))
                        if len(grams & other) >= 2 else 0.0 for other in self.grams]
        lex_max, exp_max = max(lexical, default=0), max(expanded, default=0)
        # Rare, explicit qualifiers such as a failure condition/operation channel should
        # survive synonym expansion. Equivalent alias words do not become hard anchors.
        qualifiers = {word for word in meaningful(query) - self.alias_terms if self.df.get(word, 0)}
        rare = min((self.df[word] for word in qualifiers), default=0)
        anchors = {word for word in qualifiers if self.df[word] == rare}
        active_groups = [set(word for alias in group for word in meaningful(alias))
                         for group in SYNONYM_GROUPS if any(alias in query for alias in group)]
        candidates = []
        for index, terms in enumerate(self.terms):
            if anchors and not anchors & terms.keys():
                continue
            if not lexical[index] and not expanded[index]:
                continue
            strength = (0.65 * lexical[index] / lex_max if lex_max else 0.0)
            strength += 0.25 * expanded[index] / exp_max if exp_max else 0.0
            strength += 0.10 * similarities[index]
            strength += 0.10 * sum(bool(group & terms.keys()) for group in active_groups)
            candidates.append((index, strength))
        peak = max((strength for _, strength in candidates), default=0.0)
        ranking = sorted(((index, strength) for index, strength in candidates if strength >= 0.30 * peak),
                         key=lambda pair: (-pair[1], self.chunks[pair[0]]["doc_id"]))
        results, seen = [], set()
        for index, strength in ranking:
            chunk = self.chunks[index]
            if chunk["doc_id"] in seen:
                continue
            seen.add(chunk["doc_id"])
            results.append({**chunk, "score": round(strength, 6),
                            "channel_scores": {"bm25": round(lexical[index], 4),
                                               "expanded_bm25": round(expanded[index], 4),
                                               "ngram_dice": round(similarities[index], 4),
                                               "relative_support": round(strength / peak, 4)}})
            if len(results) >= top_k:
                break
        return results
