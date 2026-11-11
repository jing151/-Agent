"""Coordinate coverage and support after schema and citation authentication.

Errors retain the legacy umbrella code at the Agent boundary, while layer
metadata distinguishes mapping defects from unsupported propositions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .claim_mapping import check_coverage
from .claim_support import check_claim_support, unresolved_conflicts
from .evidence_rules import (UNCERTAINTY, INFERENCE_MARKER, GENERALIZATION, ASSERTION, OUTCOME, TIMED_PAYOUT,
    LOGIC_WORDS, normalized, units, unframe, literal_support, drops_explicit_condition,
    necessity_direction_supported, boundary_errors)
from .schemas import AnswerSegment, Citation, ClaimEvidence, QuestionInput


@dataclass
class SupportReview:
    claims: list[ClaimEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: str = "legacy_unclassified"
    coverage_errors: list[str] = field(default_factory=list)
    support_errors: list[str] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    unmapped_fragments: list[dict] = field(default_factory=list)
    support_proofs: list[dict] = field(default_factory=list)
    control_bindings: list[dict] = field(default_factory=list)

    @property
    def failure_stage(self) -> str:
        if "claim_mapping_required" in self.coverage_errors:
            return "schema_validation"
        return "answer_claim_coverage" if self.coverage_errors else "evidence_support"

    def public(self) -> dict:
        return {"coverage": self.coverage, "errors": self.errors, "warnings": self.warnings,
            "layers": {"schema": "claim_mapping_required" if "claim_mapping_required" in self.coverage_errors else "passed_before_review", "citation_integrity": "passed_before_review",
                "answer_claim_coverage": {"errors": self.coverage_errors, "bindings": self.bindings,
                                          "unmapped_fragments": self.unmapped_fragments,"control_bindings":self.control_bindings},
                "evidence_support": {"errors": self.support_errors,"bindings":self.support_proofs}},
            "mechanism": "checked IDs/segments, bounded normalized alignment, independent claim support",
            "limitation": "Not general entailment; unsupported/unrecognized relations require repair or review."}


def review_answer(answer: str, citations: list[Citation], gaps: list[str], status: str,
                  claims: list[ClaimEvidence] | None = None, *, require_claims: bool = False,
                  answer_segments: list[AnswerSegment] | None = None, parts: dict[str, str] | None = None,
                  question: str | None = None, citation_metadata: list[dict] | None = None,
                  read_documents: list[dict] | None = None, request: QuestionInput | None = None) -> SupportReview:
    quotes = [citation.quote for citation in citations]
    result = SupportReview(support_errors=boundary_errors(answer, quotes))
    if status == "insufficient_evidence" and not gaps:
        if claims:
            result.support_errors.append("insufficient_evidence_requires_gaps")
        else:
            result.warnings.append("legacy_insufficient_evidence_missing_gaps")
    if not claims:
        if require_claims:
            result.coverage_errors.append("claim_mapping_required")
        for part in units(answer):
            indexes = [i for i, quote in enumerate(quotes) if literal_support(part, [quote])]
            if indexes:
                result.claims.append(ClaimEvidence(text=part, kind="DIRECT", citation_indices=indexes))
            elif UNCERTAINTY.search(part):
                result.claims.append(ClaimEvidence(text=part, kind="UNKNOWN"))
        result.warnings.append("legacy_claim_map_partial; absence of a blocked pattern is not semantic verification")
    else:
        result.claims, result.coverage = claims, "declared_claims_checked"
        alignment = check_coverage(answer, claims, metadata=citation_metadata, segments=answer_segments, parts=parts, question=question)
        result.coverage_errors.extend(alignment.errors)
        result.bindings = alignment.bindings
        result.unmapped_fragments = alignment.unmapped_fragments
        result.control_bindings = alignment.control_bindings
        errors, warnings = check_claim_support(claims, citations, gaps, metadata=citation_metadata, bindings=alignment.bindings,
            request=request,read_documents=read_documents,proofs=result.support_proofs)
        result.support_errors.extend(errors)
        result.warnings.extend(warnings)
        if alignment.affirmative_preface:
            positive = [claim for claim in claims if claim.kind == "DIRECT" and not re.search(r"不|未|无法|不能|并非", claim.text)]
            focus = re.findall(r"生效|到账|获得|支持", question or "")
            if not positive or focus and not any(all(word in claim.text for word in focus) for claim in positive):
                result.support_errors.append("affirmative_preface_not_established")
    pairs = unresolved_conflicts(read_documents or [])
    if pairs and status == "completed":
        result.support_errors.append("unresolved_document_conflict")
    if pairs and status == "needs_clarification":
        required = {doc_id for pair in pairs for doc_id in pair}
        if not required <= {citation.doc_id for citation in citations}:
            result.support_errors.append("unresolved_conflict_requires_both_citations")
    result.coverage_errors = list(dict.fromkeys(result.coverage_errors))
    result.support_errors = list(dict.fromkeys(result.support_errors))
    result.errors = result.coverage_errors + result.support_errors
    return result
