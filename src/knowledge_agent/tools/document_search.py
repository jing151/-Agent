from __future__ import annotations

from collections import Counter
from typing import Any

from ..retrieval.generalized import GeneralizingRetriever
from .document_reader import DocumentStore, ToolError


class DocumentTools:
    def __init__(self, store: DocumentStore, failures: dict[str, int] | None = None):
        self.store = store
        self.retriever = GeneralizingRetriever(store)
        # -1 = persistent failure; N = fail first N calls. One invocation per model decision.
        self.failures = failures or {}
        self.calls: Counter[str] = Counter()

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls[name] += 1
        fail_count = self.failures.get(name, 0)
        if fail_count == -1 or self.calls[name] <= fail_count:
            raise ToolError("simulated_tool_failure", f"{name} failed (injected for verification).",
                            retryable=fail_count != -1)
        if name == "search_documents":
            return {"query": arguments["query"], "results": self.retriever.search(**arguments)}
        if name == "read_document":
            return {"document": self.store.read_document(**arguments)}
        raise ToolError("unknown_tool", "Tool is not supported.")
