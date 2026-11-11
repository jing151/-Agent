"""Validated external inputs, model actions, and public results."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Status = Literal["completed", "needs_clarification", "insufficient_evidence", "tool_failed", "max_steps_reached", "validation_failed"]
Action = Literal["SEARCH", "READ", "ASK_CLARIFICATION", "ANSWER"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class QuestionInput(StrictModel):
    question: str = Field(min_length=1, max_length=12000)
    history: list[Message] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value


class Section(StrictModel):
    section_id: str = Field(min_length=1)
    title: str
    content: str = Field(min_length=1)


class Document(StrictModel):
    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^\w[\w.:-]*$")
    title: str = Field(min_length=1)
    doc_type: Literal["FAQ", "POLICY"]
    version_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: str | None
    priority: int | None
    supersedes: list[str]
    sections: list[Section] = Field(min_length=1)
    content: str = Field(min_length=1)


class Citation(StrictModel):
    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^\w[\w.:-]*$")
    version_date: str
    quote: str = Field(min_length=2, max_length=5000)

    @field_validator("quote")
    @classmethod
    def substantive(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("quote must contain at least two non-whitespace characters")
        return value


class ClaimEvidence(StrictModel):
    """A model-declared public claim, not an internal reasoning transcript."""
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["DIRECT", "INFERENCE", "UNKNOWN"]
    citation_indices: list[int] = Field(default_factory=list, max_length=12)
    rationale: str = Field(default="", max_length=240)
    claim_id: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

    @field_validator("citation_indices", mode="before")
    @classmethod
    def valid_indices(cls, value):
        if not isinstance(value, list) or any(type(index) is not int or index < 0 for index in value):
            raise ValueError("citation indices must be non-negative integers")
        return value


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=5, strict=True)


class ReadArgs(StrictModel):
    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^\w[\w.:-]*$")
    section_id: str | None = None


class AnswerSegment(StrictModel):
    """Explicit links do not prove support; every segment is checked separately."""
    segment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    part: Literal["answer", "reason"] = "answer"
    text: str = Field(min_length=1, max_length=12000)
    claim_ids: list[str] = Field(default_factory=list, max_length=24)


class AnswerArgs(StrictModel):
    answer: str = Field(min_length=1, max_length=12000)
    citations: list[Citation] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    status: Status
    claims: list[ClaimEvidence] = Field(default_factory=list, max_length=24)
    answer_segments: list[AnswerSegment] = Field(default_factory=list, max_length=48)


class ClarificationArgs(StrictModel):
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    claims: list[ClaimEvidence] = Field(default_factory=list, max_length=24)
    answer_segments: list[AnswerSegment] = Field(default_factory=list, max_length=48)


ARGUMENT_MODELS = {"SEARCH": SearchArgs, "READ": ReadArgs, "ANSWER": AnswerArgs,
                   "ASK_CLARIFICATION": ClarificationArgs}


class Decision(StrictModel):
    action: Action
    arguments: dict[str, Any]
    decision_summary: str = Field(min_length=1, max_length=240)


def parse_decision(raw: str | dict[str, Any] | Decision) -> Decision:
    """Validate JSON and action-specific parameters before any tool execution."""
    payload = json.loads(raw) if isinstance(raw, str) else raw
    result = payload if isinstance(payload, Decision) else Decision.model_validate(payload)
    args = ARGUMENT_MODELS[result.action].model_validate(result.arguments)
    normalized = args.model_dump(mode="json")
    # Preserve historical action JSON shape so saved real contexts stay auditable.
    for optional in ["claims", "answer_segments"]:
        if optional not in result.arguments:
            normalized.pop(optional, None)
    # New optional mapping metadata must not alter historical action JSON or
    # the input-context hashes reconstructed from saved real traces.
    for old, current in zip(result.arguments.get("claims", []), normalized.get("claims", [])):
        if "claim_id" not in old:
            current.pop("claim_id", None)
    return result.model_copy(update={"arguments": normalized})


class TraceEntry(StrictModel):
    run_id: str
    model_mode: str
    model_name: str
    step: int
    action: str
    arguments: dict[str, Any]
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    observation_summary: str
    observation: dict[str, Any]
    decision_summary: str
    next_action: str | None = None
    timestamp: str
    model_response: str | None = None
    model_metadata: dict[str, Any] = Field(default_factory=dict)
    final_status: Status | None = None


class AgentResult(StrictModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    status: Status
    trace: list[TraceEntry] = Field(default_factory=list)
    run_id: str
    model_mode: str
    model_name: str
    knowledge_base_sha256: str
    claims: list[ClaimEvidence] = Field(default_factory=list)
    evidence_review: dict[str, Any] = Field(default_factory=dict)
