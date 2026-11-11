"""Validate local tool observations before granting evidence/read authorization."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..schemas import Document
from .document_reader import DocumentStore, ToolError


def validate_tool_result(name: str, payload: Any, arguments: dict[str, Any], store: DocumentStore) -> dict[str, Any]:
    try:
        if not isinstance(payload, dict):
            raise ValueError
        if name == "read_document":
            raw = payload["document"]
            doc = Document.model_validate({key: raw[key] for key in Document.model_fields})
            canonical = store.documents[arguments["doc_id"]]
            if doc.doc_id != canonical.doc_id or doc.version_date != canonical.version_date:
                raise ValueError
            for key in ["title", "doc_type", "scope", "priority", "supersedes"]:
                if getattr(doc, key) != getattr(canonical, key):
                    raise ValueError
            section_id = arguments.get("section_id")
            expected = canonical.content if section_id is None else next(
                section.content for section in canonical.sections if section.section_id == section_id)
            if doc.content != expected or any(section.content not in expected for section in doc.sections):
                raise ValueError
        elif name == "search_documents":
            if not isinstance(payload["query"], str) or not isinstance(payload["results"], list):
                raise ValueError
            for hit in payload["results"]:
                if not all(key in hit for key in ["doc_id", "version_date", "title", "section_id", "snippet"]):
                    raise ValueError
                canonical = store.documents[hit["doc_id"]]
                if hit["version_date"] != canonical.version_date or hit["title"] != canonical.title:
                    raise ValueError
                if not isinstance(hit["snippet"], str) or hit["snippet"] not in canonical.content:
                    raise ValueError
        return payload
    except (KeyError, ValueError, TypeError, ValidationError, StopIteration):
        raise ToolError("malformed_tool_result", f"{name} returned missing or inconsistent source fields.", False) from None
