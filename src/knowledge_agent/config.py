from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 8
    max_tool_calls: int = 6
    require_claims: bool = False
    answer_quality_mode: Literal["off", "shadow", "enforce"] = "shadow"

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 8:
            raise ValueError("max_steps must be between 1 and 8")
        if not 1 <= self.max_tool_calls <= 6:
            raise ValueError("max_tool_calls must be between 1 and 6")
        if self.answer_quality_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("answer_quality_mode must be off, shadow or enforce")


@dataclass(frozen=True, repr=False)
class ModelConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float | None = 0.1
    json_mode: str = "json_object"
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> ModelConfig:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        model = os.getenv("OPENAI_MODEL", "").strip()
        if not key or not model:
            raise ValueError("Real mode requires OPENAI_API_KEY and OPENAI_MODEL; use --mode mock offline.")
        temperature = os.getenv("OPENAI_TEMPERATURE", "0.1").strip()
        mode = os.getenv("OPENAI_JSON_MODE", "json_object")
        if mode not in {"json_object", "text"}:
            raise ValueError("OPENAI_JSON_MODE must be json_object or text")
        return cls(key, os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"), model,
                   float(temperature) if temperature else None, mode,
                   float(os.getenv("OPENAI_TIMEOUT", "30")))
