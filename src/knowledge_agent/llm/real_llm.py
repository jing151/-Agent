"""OpenAI-compatible Chat Completions over HTTP; each decide sends a new request."""
from __future__ import annotations

import json
import hashlib
from typing import Any

import httpx

from ..config import ModelConfig
from .base import ModelError, SYSTEM_PROMPT


class RealLLM:
    mode = "real"

    def __init__(self, config: ModelConfig, client: httpx.Client | None = None):
        self.config = config
        self.name = config.model
        self.last_response_metadata: dict[str, Any] = {}
        # No HTTP retries and no fallback to Mock. Tool recovery belongs to the model.
        self.client = client or httpx.Client(timeout=config.timeout, transport=httpx.HTTPTransport(retries=0))
        self._owns_client = client is None

    def decide(self, context: dict[str, Any]) -> str:
        self.last_response_metadata = {
            "requested_model": self.config.model,
            "input_context_sha256": hashlib.sha256(json.dumps(
                context, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "observation_count": len(context.get("observations", [])),
        }
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        }
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.json_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        try:
            response = self.client.post(
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.config.api_key}, json=body)
            response.raise_for_status()
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ModelError("Model returned no JSON text (empty/refused response).")
            if choice.get("finish_reason") == "length":
                raise ModelError("Model output was truncated; no answer accepted.")
            # Keep only audit metadata and final JSON text. Never retain reasoning_content,
            # request headers, keys, or the full provider response.
            provider_model = payload.get("model")
            if isinstance(provider_model, str) and provider_model:
                self.name = provider_model
            self.last_response_metadata.update({
                "provider_model": provider_model,
                "response_id": payload.get("id"),
                "finish_reason": choice.get("finish_reason"),
                "usage": payload.get("usage", {}),
            })
            return content
        except httpx.HTTPStatusError as exc:
            # Never include request headers, server body, URL queries, or credentials.
            raise ModelError(f"Model API HTTP {exc.response.status_code}; no automatic retry.") from None
        except httpx.RequestError:
            raise ModelError("Model API network/timeout error; no automatic retry.") from None
        except (KeyError, IndexError, ValueError, TypeError):
            raise ModelError("Model API response does not match Chat Completions format.") from None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
