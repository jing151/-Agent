from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from ..schemas import Document, Section


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class DocumentStore:
    """Load the selected file for each run and keep a consistent run-local snapshot."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        rows = json.loads(raw.decode("utf-8-sig"))
        self.documents: dict[str, Document] = {}
        self.source_warnings: dict[str, str] = {}
        for row in rows:
            document = Document.model_validate(row)
            date.fromisoformat(document.version_date)
            if document.doc_id in self.documents:
                raise ValueError(f"Duplicate doc_id: {document.doc_id}")
            ids = [section.section_id for section in document.sections]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate section_id: {document.doc_id}")
            if any(section.content not in document.content for section in document.sections):
                # Reviewers may replace only content. Never search or read obsolete copies
                # in sections; explicitly fall back to a single current full-text section.
                self.source_warnings[document.doc_id] = "sections 与 content 不一致；以当前 content 为准，旧章节不可引用。"
                document = document.model_copy(update={"sections": [Section(
                    section_id="__full__", title="当前文档全文", content=document.content)]})
            self.documents[document.doc_id] = document
        if not self.documents:
            raise ValueError("Knowledge base cannot be empty")

    def read_document(self, doc_id: str, section_id: str | None = None) -> dict[str, Any]:
        document = self.documents.get(doc_id)
        if document is None:
            raise ToolError("document_not_found", f"Document {doc_id} does not exist.")
        result = document.model_dump(mode="json")
        if doc_id in self.source_warnings:
            result["source_warning"] = self.source_warnings[doc_id]
        if section_id is not None:
            sections = [s for s in document.sections if s.section_id == section_id]
            if not sections:
                raise ToolError("section_not_found", f"Section {section_id} does not exist in {doc_id}.")
            result["content"] = sections[0].content
            result["sections"] = [s.model_dump() for s in sections]
        result["section_id"] = section_id
        return result
