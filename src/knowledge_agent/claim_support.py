"""Proposition support, independent from answer alignment and READ grants."""
from __future__ import annotations

import re

from .claim_mapping import compact, necessary_relation, unframe, word_forms
from .evidence_rules import INFERENCE_MARKER, UNCERTAINTY, absence_targets, has_uncertainty, boundary_errors, drops_explicit_condition
from .schemas import Citation, ClaimEvidence, QuestionInput
from .workflow_evidence import workflow_inference


def direct_matches(text: str, quote: str, metadata: list[dict] | None = None) -> bool:
    value = compact(word_forms(unframe(text, metadata)))
    if not value:
        return False
    source = compact(word_forms(quote))
    for match in re.finditer(re.escape(value), source):
        before, after = source[:match.start()], source[match.end():]
        if re.search(r"(?:不|未|无|不得|不能|禁止|并非|不是|无需|不必|勿)$", before):
            continue
        # Substring matching must not turn a clipped number into a different
        # quantity (15 from 15%, 1 from 1.5, or 15 from -15).
        if value[-1].isdigit() and re.match(r"\d|decimalpoint|percentoperator|(?:个)?(?:工作日|自然日|小时|个月|元|万元|天|日|年)", after):
            continue
        if value[0].isdigit() and re.search(r"\d$|(?:decimalpoint|minusoperator|plusoperator|lessoperator|greateroperator|lessequaloperator|greaterequaloperator)$", before):
            continue
        return True
    return False


def drops_scope(text: str, quote: str) -> bool:
    for match in re.finditer(r"(?:仅|只有|仅限)(.{1,18}?)(?:可以|可|能够|能)申请", quote):
        if "申请" in text and compact(match.group(1)) not in compact(text):
            return True
    return False


def supported_necessity(text: str, quotes: list[str]) -> bool:
    relation = necessary_relation(text)
    if not relation:
        return False
    condition, outcome = relation
    for quote in quotes:
        parts = re.split(r"后才能|后方可|后才可", quote, maxsplit=1)
        if len(parts) == 2 and condition in compact(word_forms(parts[0])) and outcome in compact(word_forms(parts[1])):
            return True
    return False


def check_claim_support(claims: list[ClaimEvidence], citations: list[Citation], gaps: list[str],
                        *, metadata: list[dict] | None = None, bindings: list[dict] | None = None,
                        request: QuestionInput | None = None, read_documents: list[dict] | None = None,
                        proofs: list[dict] | None = None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for index, claim in enumerate(claims):
        prefix = f"claim_{index}:"
        if any(ref >= len(citations) for ref in claim.citation_indices):
            errors.append(prefix + "unknown_citation_index")
            continue
        supporting = [citations[ref].quote for ref in claim.citation_indices]
        own_metadata = [metadata[ref] for ref in claim.citation_indices] if metadata else None
        if claim.kind == "DIRECT":
            matched = [quote for quote in supporting if direct_matches(claim.text, quote, own_metadata)]
            if not matched:
                errors.append(prefix + "direct_not_in_cited_quote")
            elif all(drops_explicit_condition(claim.text, quote) or drops_scope(claim.text, quote) for quote in matched):
                errors.append(prefix + "direct_drops_explicit_condition")
        elif claim.kind == "INFERENCE":
            public_forms = (bindings[index].get("public_relation_texts") or [claim.text]) if bindings else [claim.text]
            workflow=workflow_inference(claim.text,supporting,request)
            marked=bool(INFERENCE_MARKER.search(claim.text)) or bool(workflow and workflow["rule"]=="user_condition_excludes_this_workflow")
            forms_marked=all(INFERENCE_MARKER.search(text) or workflow_inference(text,supporting,request) for text in public_forms)
            if not supporting or not claim.rationale.strip() or not marked or not forms_marked:
                errors.append(prefix + "inference_requires_basis_and_explicit_marker")
            if not workflow and not supported_necessity(claim.text, supporting):
                errors.append(prefix + "necessary_condition_direction_not_supported")
            if workflow and proofs is not None:
                proofs.append({"claim_index":index,"citation_indices":claim.citation_indices,**workflow})
            errors.extend(prefix + issue for issue in boundary_errors(claim.text, supporting))
            warnings.append(prefix + "bounded_necessary_condition_rule_not_general_entailment")
        else:
            if not has_uncertainty(claim.text):
                errors.append(prefix + "unknown_requires_explicit_uncertainty")
            if not gaps:
                errors.append(prefix + "unknown_requires_gaps")
            scoped_ids={citations[ref].doc_id for ref in claim.citation_indices}
            read_texts=[doc["content"] for doc in read_documents or [] if not scoped_ids or doc["doc_id"] in scoped_ids]
            errors.extend(prefix + issue for issue in boundary_errors(claim.text, [],summary_sources=read_texts))
            if any(direct_matches(target,source) for target in absence_targets(claim.text) for source in read_texts):
                errors.append(prefix + "absence_contradicted_by_read_evidence")
    return list(dict.fromkeys(errors)), warnings


def unresolved_conflicts(reads: list[dict]) -> list[tuple[str, str]]:
    """Narrow same-body/different-value POLICY conflicts; no ID/date shortcuts."""
    docs = list({item["doc_id"]: item for item in reads if item.get("doc_type") == "POLICY"}.values())
    result = []
    for index, left in enumerate(docs):
        for right in docs[index + 1:]:
            a, b = compact(left["content"]), compact(right["content"])
            numbers = r"\d+(?:decimalpoint\d+)?"
            values_a, values_b = re.findall(numbers, a), re.findall(numbers, b)
            if not values_a or values_a == values_b or re.sub(numbers, "NUMBER", a) != re.sub(numbers, "NUMBER", b):
                continue
            if left["doc_id"] in right.get("supersedes", []) or right["doc_id"] in left.get("supersedes", []):
                continue
            if left.get("priority") is not None and right.get("priority") is not None and left["priority"] != right["priority"]:
                continue
            result.append((left["doc_id"], right["doc_id"]))
    return result
