from __future__ import annotations

from typing import Any

from .schemas import Citation, ClaimEvidence
from .tools.document_reader import DocumentStore


def validate_citations(citations: list[Citation], store: DocumentStore,
                       read_evidence: list[dict[str, Any]],
                       claims: list[ClaimEvidence] | None = None) -> list[str]:
    """Check exact quotations against *successful reads*, not search results or unseen sections."""
    errors: list[str] = []
    for citation in citations:
        doc = store.documents.get(citation.doc_id)
        if doc is None:
            errors.append(f"unknown_doc_id:{citation.doc_id}")
            continue
        reads = [read for read in read_evidence if read.get("doc_id") == citation.doc_id]
        if not reads:
            errors.append(f"document_not_read:{citation.doc_id}")
        if citation.version_date != doc.version_date:
            errors.append(f"version_mismatch:{citation.doc_id}")
        if reads and not any(isinstance(read.get("content"), str) and citation.quote in read["content"] for read in reads):
            errors.append(f"quote_not_in_read_text:{citation.doc_id}")
    for index, claim in enumerate(claims or []):
        if any(ref >= len(citations) for ref in claim.citation_indices):
            errors.append(f"claim_{index}:unknown_citation_index")
    return errors
