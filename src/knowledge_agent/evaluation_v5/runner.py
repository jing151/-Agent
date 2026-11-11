"""Thin instrumentation around the unchanged Agent, RealLLM and document tools.

No fallback query, action policy or network transport is supplied here. Offline
mode requires MockTransport. Live mode additionally requires the official
ledger's explicit authorization; this module never grants it or loads .env.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ..agent import KnowledgeAgent
from ..config import AgentConfig, ModelConfig
from ..llm.base import ModelError
from ..llm.real_llm import RealLLM
from ..schemas import AgentResult, QuestionInput, parse_decision
from ..tools.document_reader import DocumentStore
from ..tools.document_search import DocumentTools
from .budget import BudgetError, BudgetLedger, LIVE_LEDGER, now


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class Redactor:
    """Persist only selected fields; never serialize an HTTP request/response object."""
    forbidden_keys = {"authorization", "api_key", "apikey", "api-key", "headers", "reasoning_content",
                      "reasoning", "chain_of_thought", "analysis", "thinking"}

    def __init__(self, secret: str):
        self.secret = secret

    def clean(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self.clean(item) for key, item in value.items()
                    if str(key).lower() not in self.forbidden_keys}
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            if self.secret:
                value = value.replace(self.secret, "[REDACTED]")
            return re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "[REDACTED]", value)
        return value


class Artifacts:
    def __init__(self, directory: Path, execution_id: str, redactor: Redactor):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", execution_id):
            raise ValueError("Invalid execution ID")
        self.directory, self.execution_id, self.redactor = directory.resolve(), execution_id, redactor
        self.input_path = self.directory / f"inputs/{execution_id}.json"
        self.output_path = self.directory / f"outputs/{execution_id}.json"
        self.trace_path = self.directory / f"traces/{execution_id}.json"
        self.api_directory = self.directory / f"traces/api/{execution_id}"
        for path in [self.input_path, self.output_path, self.trace_path, self.api_directory]:
            if path.exists():
                raise FileExistsError("Existing run artifacts will not be overwritten")
        marker = self.directory / f"reports/run-claims/{execution_id}"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.mkdir()  # Exclusive cross-process run claim, retained even on failure.

    def write(self, path: Path, value: Any) -> None:
        if not path.resolve().is_relative_to(self.directory):
            raise ValueError("Artifact path escapes evaluation directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(self.redactor.clean(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")


def usage_metadata(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    # Numbers only: an unexpected provider string cannot smuggle diagnostics.
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
               "prompt_tokens_details", "completion_tokens_details", "cached_tokens", "reasoning_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"}
    return {key: usage_metadata(value) if isinstance(value, dict) else value for key, value in raw.items()
            if key in allowed and (type(value) is int and value >= 0 or isinstance(value, dict))}


def response_metadata(response: httpx.Response, redactor: Redactor) -> dict:
    data: dict[str, Any] = {"http_status": response.status_code, "response_id": None,
                            "provider_model": None, "usage": None, "raw_action_json": None}
    if response.status_code >= 400:
        return {**data, "outcome": "http_error", "error_code": f"http_{response.status_code}"}
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError
        data.update(response_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
                    provider_model=payload.get("model") if isinstance(payload.get("model"), str) else None,
                    usage=usage_metadata(payload.get("usage")))
        if "error" in payload:
            return redactor.clean({**data, "outcome": "provider_error", "error_code": "provider_error"})
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        finish = choice.get("finish_reason")
        data["finish_reason"] = finish if finish in {"stop", "length", "content_filter", "tool_calls"} else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        try:
            parse_decision(content)
        except (ValueError, TypeError):
            # Invalid/free-form text might contain reasoning. Keep a fingerprint,
            # never save it as a claimed action JSON or hidden chain of thought.
            data["invalid_content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
            return redactor.clean({**data, "outcome": "invalid_action", "error_code": "invalid_action_json"})
        data["raw_action_json"] = content  # Exact action-channel text, except credential redaction.
        return redactor.clean({**data, "outcome": "model_error" if finish == "length" else "completed",
                               "error_code": "truncated_response" if finish == "length" else None})
    except (KeyError, IndexError, ValueError, TypeError):
        return redactor.clean({**data, "outcome": "invalid_response", "error_code": "invalid_response"})


class CapturingTransport(httpx.BaseTransport):
    """Capture at the HTTP boundary and reserve budget BEFORE delegation."""
    def __init__(self, delegate: httpx.BaseTransport, budget: BudgetLedger, artifacts: Artifacts,
                 case_id: str, evaluation_mode: str):
        self.delegate, self.budget, self.artifacts = delegate, budget, artifacts
        self.case_id, self.evaluation_mode = case_id, evaluation_mode
        self.records: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.budget.guard()
        index = len(self.records) + 1
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        request_record = {"execution_id": self.artifacts.execution_id, "decision_index": index,
                          "model_mode": "mock" if self.budget.simulation else "real", "evaluation_mode": self.evaluation_mode,
                          "captured_at": now(), "method": request.method,
                          "actual_request_body": body, "context": context, "input_context_sha256": digest(context),
                          "dispatch_state": "captured_before_budget_reservation"}
        # Fail closed before spending a slot if the trace path is occupied.
        self.artifacts.write(self.artifacts.api_directory / f"{index:02d}-request.json", request_record)
        number = self.budget.reserve(self.artifacts.execution_id, self.case_id)
        record = {**self.artifacts.redactor.clean(request_record), "budget_request_number": number}
        self.records.append(record)
        started = time.perf_counter()
        metadata = {"outcome": "transport_error", "error_code": "transport_error", "http_status": None,
                    "response_id": None, "provider_model": None, "usage": None}
        try:
            response = self.delegate.handle_request(request)
            response.read()
            metadata = response_metadata(response, self.artifacts.redactor)
            return response
        except httpx.RequestError as error:
            metadata["error_code"] = "timeout" if isinstance(error, httpx.TimeoutException) else "network_error"
            raise
        except ModelError:
            metadata.update(outcome="model_error", error_code="model_error")
            raise
        finally:
            metadata["elapsed_seconds"] = time.perf_counter() - started
            record.update(metadata)
            details = {key: value for key, value in metadata.items() if key in {
                "outcome", "error_code", "http_status", "response_id", "provider_model", "usage", "elapsed_seconds"}}
            self.budget.finish(number, details)
            self.artifacts.write(self.artifacts.api_directory / f"{index:02d}-response.json", {
                "execution_id": self.artifacts.execution_id, "decision_index": index,
                "budget_request_number": number, "model_mode": request_record["model_mode"],
                "evaluation_mode": self.evaluation_mode, **metadata})

    def close(self) -> None:
        pass  # The caller owns the injected delegate, which may span multiple runs.


class ObservedModel:
    """Delegate every decision unchanged; add audit metadata without a policy."""
    def __init__(self, model: RealLLM, transport: CapturingTransport, simulation: bool):
        self.model, self.transport = model, transport
        self.mode = "mock" if simulation else "real"
        self.last_response_metadata: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self.last_response_metadata.get("provider_model") or self.model.config.model

    def decide(self, context: dict[str, Any]) -> str:
        self.last_response_metadata = {"input_context_sha256": digest(context),
                                       "observation_count": len(context.get("observations", [])),
                                       "requested_model": self.model.config.model}
        if set(context) != {"question", "history", "observations", "budget"}:
            raise ModelError("unexpected_model_context_keys")
        count = len(self.transport.records)
        try:
            return self.model.decide(context)
        finally:
            if len(self.transport.records) > count:
                item = self.transport.records[-1]
                self.last_response_metadata.update({key: item.get(key) for key in [
                    "budget_request_number", "decision_index", "response_id", "provider_model", "usage",
                    "http_status", "elapsed_seconds", "outcome", "error_code"]})


def classify_recovery(result: AgentResult, request: QuestionInput, case: dict, records: list[dict]) -> dict:
    """Posthoc labels only; nothing from this function enters a model request."""
    searches = [step for step in result.trace if step.action == "SEARCH"]
    targets = set(case.get("required_evidence_doc_ids", []))
    reads = {step.arguments["doc_id"] for step in result.trace if step.action == "READ" and step.observation["ok"]}
    citations = {item.doc_id for item in result.citations}
    common = {"read_doc_ids": sorted(reads), "citation_doc_ids": sorted(citations),
              "evidence_repair_observed": any(step.observation.get("error", {}).get("code") in {
                  "citation_validation_failed", "evidence_support_failed"} for step in result.trace),
              "fault_exercised": any(step.observation.get("error", {}).get("code") == "simulated_tool_failure" for step in result.trace)}
    if not searches:
        return {**common, "label": "no_search", "successful_dynamic_recovery": False, "search_queries": []}

    def hit_ids(step):
        return {hit["doc_id"] for hit in step.observation.get("result", {}).get("results", [])}
    first = searches[0]
    first_ids = hit_ids(first)
    original = first.arguments["query"].strip() == request.question.strip()
    first_bad = not first.observation["ok"] or not first_ids or bool(targets and not targets & first_ids)
    first_observation = {"step": first.step, "action": first.action, "arguments": first.arguments, **first.observation}
    changed = [step for step in searches[1:] if step.arguments["query"].strip() != first.arguments["query"].strip()]
    improved = [step for step in changed if (hit_ids(step) - first_ids) & targets]
    delivered = any(first_observation in record["context"]["observations"] for record in records
                    if any(record.get("decision_index") == step.model_metadata.get("decision_index") for step in improved))
    failure_delivered = first_bad and any(first_observation in record["context"]["observations"] for record in records
                    if any(record.get("decision_index") == step.model_metadata.get("decision_index") for step in searches[1:]))
    same_query_retry = not first.observation["ok"] and any(step.observation["ok"]
                    and step.arguments["query"].strip() == first.arguments["query"].strip() for step in searches[1:])
    union = set().union(*(hit_ids(step) for step in searches))
    business = (result.status in case.get("expected_statuses", []) and targets <= reads and targets <= citations)
    recovery = first_bad and bool(improved) and delivered and targets <= union and business
    strict = recovery and (original or not case.get("require_original_first_search"))
    label = ("dynamic_recovery" if strict else "proactive_rewrite" if not original
             else "same_query_tool_retry" if same_query_retry
             else "safe_stop_without_recovery" if result.status == "tool_failed"
             else "first_search_success" if not first_bad else "not_recovered")
    return {**common, "label": label, "successful_dynamic_recovery": bool(strict), "recovery_after_observation": bool(recovery),
            "original_first_search": original, "first_search_failed_or_unrelated": first_bad,
            "first_search_empty": first.observation["ok"] and not first_ids,
            "first_search_unrelated": bool(targets and first_ids and not targets & first_ids),
            "search_queries": [step.arguments["query"] for step in searches], "second_query_changed": bool(changed),
            "new_relevant_doc_ids": sorted(set().union(*(hit_ids(step) for step in improved)) & targets - first_ids),
            "failure_observation_delivered": bool(failure_delivered), "same_query_tool_retry": same_query_retry,
            "final_retrieved_doc_ids": sorted(union),
            "full_target_recall": targets <= union if targets else None,
            "business_evidence_gate": business}


class V5Runner:
    """A single case runner; test-plan expectations are used only AFTER Agent.run."""
    def __init__(self, directory: Path, model_config: ModelConfig, transport: httpx.BaseTransport | None = None,
                 *, mode: str = "offline"):
        if mode not in {"offline", "real"}:
            raise ValueError("Unsupported evaluation mode")
        if mode == "offline" and not isinstance(transport, httpx.MockTransport):
            raise ValueError("Offline evaluation requires MockTransport; no default network transport exists")
        if mode == "real" and isinstance(transport, httpx.MockTransport):
            raise ValueError("MockTransport cannot produce real evidence")
        if mode == "real" and transport is not None:
            raise ValueError("Live runner owns its retry-free transport; do not inject one")
        self.directory = Path(directory).resolve()
        if mode == "offline" and self.directory == LIVE_LEDGER.parent.resolve():
            raise BudgetError("offline_artifacts_require_separate_directory")
        self.budget = BudgetLedger(self.directory / "api-budget.json", simulation=mode == "offline")
        self.mode, self.model_config, self.transport = mode, model_config, transport
        self.config = AgentConfig(max_steps=8, max_tool_calls=6, require_claims=True)
        self._turns: dict[str, tuple[QuestionInput, AgentResult]] = {}

    def request_for_case(self, case: dict) -> tuple[QuestionInput, str | None]:
        if case.get("depends_on"):
            source = self._turns.get(case["depends_on"])
            if source is None or source[1].status != "completed":
                raise ModelError("actual_predecessor_unavailable")
            previous_request, previous_result = source
            history = [*previous_request.model_dump(mode="json")["history"],
                       {"role": "user", "content": previous_request.question},
                       {"role": "assistant", "content": previous_result.answer}]
            return QuestionInput(question=case["input_template"]["question"], history=history), previous_result.run_id
        return QuestionInput.model_validate(case["input"]), None

    def run_case(self, case: dict, store: DocumentStore, *, execution_id: str | None = None) -> dict:
        self.budget.guard()  # No client construction before the live authorization gate.
        request, previous_run_id = self.request_for_case(case)
        case_id = case["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case_id):
            raise ValueError("Invalid case ID")
        execution_id = execution_id or f"{case_id}-{uuid4().hex}"
        if self.model_config.api_key and any(self.model_config.api_key in name for name in [case_id, execution_id]):
            raise ValueError("Sensitive value in execution identifier")
        artifacts = Artifacts(self.directory, execution_id, Redactor(self.model_config.api_key))
        fault = case.get("fault_injection")
        if fault and (case_id != "H1" or fault != {"search_documents": 1}):
            raise ValueError("Only the planned H1 first-SEARCH local fault is supported")
        evaluation_mode = ("offline_mock_transport" if self.mode == "offline" else "real") + ("_fault_injection" if fault else "")
        tools = DocumentTools(store, failures=deepcopy(fault))
        artifacts.write(artifacts.input_path, {"execution_id": execution_id, "case_id": case_id,
                        "input": request.model_dump(mode="json"), "previous_agent_run_id": previous_run_id,
                        "evaluation_mode": evaluation_mode, "max_steps": 8, "max_tool_calls": 6, "require_claims": True,
                        "knowledge_base_sha256": store.sha256})
        # The only live transport construction is AFTER guard() and validation.
        # Owning it prevents a caller from hiding extra retries inside one slot.
        delegate = self.transport if self.mode == "offline" else httpx.HTTPTransport(retries=0)
        capture = CapturingTransport(delegate, self.budget, artifacts, case_id, evaluation_mode)
        try:
            with httpx.Client(transport=capture, timeout=self.model_config.timeout, trust_env=False) as client:
                model = ObservedModel(RealLLM(self.model_config, client=client), capture, self.mode == "offline")
                result = KnowledgeAgent(model, tools, self.config).run(request)
        finally:
            if self.mode == "real":
                delegate.close()
        recovery = classify_recovery(result, request, case, capture.records)
        report = {"execution_id": execution_id, "run_id": result.run_id, "case_id": case_id,
                  "evaluation_mode": evaluation_mode, "model_mode": result.model_mode,
                  "input": request.model_dump(mode="json"), "previous_agent_run_id": previous_run_id,
                  "result": result.model_dump(mode="json"), "recovery": recovery,
                  "tool_calls": dict(tools.calls), "http_attempts": len(capture.records),
                  "stop_reason": "phase_budget_exhausted" if any("phase_api_budget_exhausted" in str(step.observation)
                                for step in result.trace) else None}
        artifacts.write(artifacts.output_path, report)
        artifacts.write(artifacts.trace_path, {"execution_id": execution_id, "run_id": result.run_id,
                        "model_mode": result.model_mode, "model_name": result.model_name,
                        "evaluation_mode": evaluation_mode, "final_status": result.status,
                        "steps": [step.model_dump(mode="json") for step in result.trace],
                        "api_records": capture.records})
        artifacts.write(artifacts.api_directory / "manifest.json", {"execution_id": execution_id,
                        "run_id": result.run_id, "evaluation_mode": evaluation_mode,
                        "requests": [{"decision_index": row["decision_index"], "budget_request_number": row["budget_request_number"]}
                                     for row in capture.records],
                        "note": "Agent creates run_id internally; immutable pre-HTTP journals use execution_id and join here after run completion."})
        self._turns[case_id] = (request, result)
        return artifacts.redactor.clean(report)

    def run_plan(self, plan: dict, root: Path) -> dict:
        """Case scheduling only: no tool sequence, query rewriting or retries."""
        if self.mode == "real":
            # Authorization failure must not create a batch of purported runs.
            self.budget.guard()
        by_id = {item["id"]: item for item in plan["cases"]}
        order = plan.get("execution_order", list(by_id))
        entries = []
        stopped = None
        for case_id in order:
            if stopped:
                entries.append({"case_id": case_id, "execution_status": "not_executed", "reason": stopped})
                continue
            try:
                self.budget.guard()
            except BudgetError as error:
                if str(error) != "phase_api_budget_exhausted":
                    raise
                stopped = "phase_budget_exhausted"
                entries.append({"case_id": case_id, "execution_status": "not_executed", "reason": stopped})
                continue
            specimen = by_id[case_id]
            kb = (Path(root) / specimen.get("knowledge_base", "data/documents.json")).resolve()
            if not kb.is_relative_to(Path(root).resolve()):
                raise ValueError("Knowledge base escapes project root")
            try:
                report = self.run_case(specimen, DocumentStore(kb))
            except ModelError as error:
                if str(error) != "actual_predecessor_unavailable":
                    raise
                entries.append({"case_id": case_id, "execution_status": "not_executed", "reason": "actual_predecessor_unavailable"})
                continue
            entries.append({"case_id": case_id, "execution_status": "executed", "execution_id": report["execution_id"],
                            "run_id": report["run_id"], "status": report["result"]["status"],
                            "http_attempts": report["http_attempts"], "recovery": report["recovery"]})
            if report["stop_reason"]:
                stopped = report["stop_reason"]
            elif any(step["action"] == "MODEL_ERROR" for step in report["result"]["trace"]):
                stopped = "model_service_or_protocol_failure"
        batch = {"evaluation_mode": "offline_mock_transport" if self.mode == "offline" else "real",
                 "entries": entries, "stop_reason": stopped, "budget_attempts": self.budget.data["attempts"]}
        path = self.directory / f"reports/batches/{uuid4().hex}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(Redactor(self.model_config.api_key).clean(batch), stream, ensure_ascii=False, indent=2)
        return batch
