"""Run real API acceptance cases and retain auditable, non-scripted model decisions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import KnowledgeAgent
from .citation_validator import validate_citations
from .config import AgentConfig, ModelConfig
from .llm.real_llm import RealLLM
from .schemas import AgentResult, QuestionInput, parse_decision
from .tools.document_reader import DocumentStore
from .tools.document_search import DocumentTools


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def successful_reads(result: AgentResult, doc_id: str | None = None) -> list[dict[str, Any]]:
    """Summarize every successful read; optional focus filtering must be explicit."""
    return [step.observation["result"]["document"] for step in result.trace
            if step.action == "READ" and step.observation["ok"]
            and (doc_id is None or step.observation["result"]["document"]["doc_id"] == doc_id)]


def audit_run(result: AgentResult, request: QuestionInput, store: DocumentStore,
              config: AgentConfig | None = None) -> dict[str, bool]:
    """Validate trace integrity without requiring one predetermined action sequence."""
    observations: list[dict[str, Any]] = []
    config = config or AgentConfig()
    reads: list[dict[str, Any]] = []
    contexts_match, responses_match = True, True
    tool_count = 0
    for index, step in enumerate(result.trace):
        context = {**request.model_dump(mode="json"), "observations": observations,
                   "budget": {"decisions_remaining": config.max_steps - index, "tool_calls_remaining": config.max_tool_calls - tool_count}}
        digest = hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        contexts_match &= (step.model_metadata.get("input_context_sha256") == digest
                           and step.model_metadata.get("observation_count") == len(observations))
        if step.model_response:
            parsed = parse_decision(step.model_response)
            responses_match &= parsed.action == step.action and parsed.arguments == step.arguments
        else:
            responses_match = False
        if step.tool_name and step.observation.get("error", {}).get("executed") is not False:
            tool_count += 1
        if step.action == "READ" and step.observation["ok"]:
            reads.append(step.observation["result"]["document"])
        observations.append({"step": step.step, "action": step.action,
                             "arguments": step.arguments, **step.observation})
    ids = [step.model_metadata.get("response_id") for step in result.trace]
    return {
        "real_mode": result.model_mode == "real" and all(step.model_mode == "real" for step in result.trace),
        "observations_reached_next_requests": bool(result.trace) and contexts_match,
        "executed_actions_match_model_json": bool(result.trace) and responses_match,
        "distinct_api_response_per_decision": bool(ids) and all(ids) and len(set(ids)) == len(ids),
        "provider_model_recorded": all(step.model_metadata.get("provider_model") == step.model_name
                                        and step.model_metadata.get("provider_model") for step in result.trace),
        "actual_next_actions": all(step.next_action == (result.trace[index + 1].action if index + 1 < len(result.trace) else "END")
                                   for index, step in enumerate(result.trace)),
        "final_status_recorded": all(step.final_status == result.status for step in result.trace),
        "citations_from_successful_reads": not validate_citations(result.citations, store, reads),
        "within_budget": len(result.trace) <= config.max_steps and tool_count <= config.max_tool_calls,
    }


def generate_real_examples(root: Path) -> int:
    examples = root / "examples"
    status_path = examples / "real/status.json"
    try:
        config = ModelConfig.from_env()
    except ValueError as exc:
        _write(status_path, {"timestamp": datetime.now(timezone.utc).isoformat(), "status": "not_run",
                            "reason": str(exc), "real_model_traces_completed": False})
        print("Real examples NOT RUN: model configuration unavailable; no API calls made.")
        return 2
    mobile = QuestionInput(question="手机上可以重置公司邮箱密码吗？")
    cases = [
        ("conflict", QuestionInput(question="员工费用报销期限是多少？"), root / "data/documents.json"),
        ("normal", QuestionInput(question="公司设备出现故障，应该在哪里提交报修？"), root / "data/documents.json"),
        ("mobile_before", mobile, root / "data/documents.json"),
        ("mobile_after", mobile, examples / "variants/documents_v2.json"),
    ]
    records: list[dict[str, Any]] = []
    results: dict[str, AgentResult] = {}
    with_model = RealLLM(config)
    try:
        for name, request, kb in cases:
            store = DocumentStore(kb)
            input_path = examples / f"outputs/real/{name}-input.json"
            output_path = examples / f"outputs/real/{name}.json"
            trace_path = examples / f"traces/real/{name}.json"
            _write(input_path, request.model_dump(mode="json"))
            result = KnowledgeAgent(with_model, DocumentTools(store), AgentConfig(require_claims=True)).run(request)
            _write(output_path, result.model_dump(mode="json"))
            _write(trace_path, {
                "schema_version": 1, "run_id": result.run_id, "model_mode": result.model_mode,
                "requested_model": config.model, "model_name": result.model_name,
                "knowledge_base_sha256": result.knowledge_base_sha256,
                "final_status": result.status, "steps": [step.model_dump(mode="json") for step in result.trace],
            })
            checks = audit_run(result, request, store)
            records.append({"case": name, "input": str(input_path.relative_to(examples)).replace("\\", "/"),
                            "output": str(output_path.relative_to(examples)).replace("\\", "/"),
                            "trace": str(trace_path.relative_to(examples)).replace("\\", "/"),
                            "knowledge_base": str(kb.relative_to(root)).replace("\\", "/"),
                            "model_name": result.model_name, "final_status": result.status,
                            "actions": [step.action for step in result.trace], "audit": checks})
            results[name] = result
            # Save every result as it occurs, including partial/failed runs.
            _write(examples / "real-manifest.json", {"model_mode": "real", "requested_model": config.model,
                                                       "cases": records})
            print(json.dumps({"case": name, "status": result.status,
                              "actions": [step.action for step in result.trace]}, ensure_ascii=False), flush=True)
            if any(step.action == "MODEL_ERROR" for step in result.trace):
                _write(status_path, {"timestamp": datetime.now(timezone.utc).isoformat(),
                                    "status": "api_failed", "failed_case": name,
                                    "real_model_traces_completed": False, "gaps": result.gaps,
                                    "note": "Stopped further API calls; partial real records retained, no Mock fallback."})
                return 1
    finally:
        with_model.close()

    conflict = results["conflict"]
    normal = results["normal"]
    before, after = results["mobile_before"], results["mobile_after"]
    read_ids = {step.arguments["doc_id"] for step in conflict.trace if step.action == "READ" and step.observation["ok"]}
    retrieved_ids = {hit["doc_id"] for step in conflict.trace if step.action == "SEARCH" and step.observation["ok"]
                     for hit in step.observation["result"]["results"]}
    acceptance = {
        "all_trace_audits_pass": all(all(row["audit"].values()) for row in records),
        "conflict_retrieved_both": {"D01", "D02"} <= retrieved_ids,
        "conflict_read_both": {"D01", "D02"} <= read_ids,
        "conflict_cites_both": {"D01", "D02"} <= {cite.doc_id for cite in conflict.citations},
        "conflict_requests_clarification": conflict.status == "needs_clarification",
        "conflict_shows_both_deadlines": "30" in conflict.answer and "15" in conflict.answer,
        "normal_completed_with_d03": normal.status == "completed" and "D03" in {cite.doc_id for cite in normal.citations},
        "normal_correct_entry": "设备报修" in normal.answer and "门户" in normal.answer,
        "same_mobile_question": cases[2][1] == cases[3][1],
        "different_knowledge_base": before.knowledge_base_sha256 != after.knowledge_base_sha256,
        "mobile_both_completed": before.status == after.status == "completed",
        "mobile_both_cite_d05": all("D05" in {cite.doc_id for cite in result.citations} for result in [before, after]),
        "mobile_answer_changed": before.answer != after.answer,
        "mobile_quotes_changed": [cite.model_dump() for cite in before.citations] != [cite.model_dump() for cite in after.citations],
        "mobile_after_denies_support": "不支持" in after.answer,
    }
    before_reads, after_reads = successful_reads(before, "D05"), successful_reads(after, "D05")
    acceptance["different_mobile_source_read"] = bool(before_reads and after_reads and before_reads[0]["content"] != after_reads[0]["content"])
    _write(examples / "outputs/real/mobile-comparison.json", {
        "question": mobile.question, "model_mode": "real",
        "before": {"answer": before.answer, "citations": [cite.model_dump() for cite in before.citations],
                   "read_documents": successful_reads(before), "knowledge_base_sha256": before.knowledge_base_sha256},
        "after": {"answer": after.answer, "citations": [cite.model_dump() for cite in after.citations],
                  "read_documents": successful_reads(after), "knowledge_base_sha256": after.knowledge_base_sha256},
        "checks": acceptance,
    })
    passed = all(acceptance.values())
    status = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "validated" if passed else "validation_failed",
              "requested_model": config.model, "provider_models": sorted({result.model_name for result in results.values()}),
              "real_model_traces_completed": passed, "completed_runs": len(records),
              "checks": acceptance, "note": "Actual API runs. Structural checks supplement manual review of answer semantics."}
    _write(status_path, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if passed else 1
