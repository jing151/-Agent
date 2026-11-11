"""Detect identical failed drafts, never treat an unsubmitted draft as a question sent."""
from __future__ import annotations

import hashlib
import json

from .claim_mapping import compact


class ClarificationGuard:
    def __init__(self):
        self.failures: dict[str, int] = {}

    def failed(self, arguments: dict, stage: str, errors: list[str]) -> bool:
        citations = arguments.get("citations", [])
        references = [(item["doc_id"], item["version_date"], compact(item["quote"])) for item in citations]
        claims = sorted((claim["kind"], compact(claim["text"]),
                         tuple(sorted(references[index] for index in claim.get("citation_indices", []) if index < len(references))))
                        for claim in arguments.get("claims", []))
        # Renaming IDs, rearranging citations or changing punctuation is not
        # new evidence/information. Material repairs to claims remain possible.
        value = {"question": compact(arguments["question"]), "answer": compact(arguments.get("answer", "")),
                 "reason": compact(arguments["reason"]), "citations": sorted(references), "claims": claims,
                 "gaps": sorted(compact(item) for item in arguments.get("gaps", [])),
                 "stage": stage, "errors": sorted(errors)}
        signature = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        self.failures[signature] = self.failures.get(signature, 0) + 1
        return self.failures[signature] >= 2
