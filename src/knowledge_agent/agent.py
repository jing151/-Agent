"""A model-driven loop. No question keywords, automatic tool retries, or forced tool ordering."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .citation_validator import validate_citations
from .answer_quality import clarification_delivery, review_quality
from .clarification_guard import ClarificationGuard
from .terminal_control import FailedAnswerGuard, ProgressTracker
from .evidence_support import review_answer
from .config import AgentConfig
from .llm.base import DecisionModel, ModelError
from .schemas import AgentResult, AnswerArgs, Citation, ClaimEvidence, ClarificationArgs, QuestionInput, TraceEntry, parse_decision
from .tools.document_reader import ToolError
from .tools.document_search import DocumentTools
from .tools.result_validator import validate_tool_result


class KnowledgeAgent:
    def __init__(self, model: DecisionModel, tools: DocumentTools, config: AgentConfig | None = None):
        self.model = model
        self.tools = tools
        self.config = config or AgentConfig()

    def run(self, request: QuestionInput) -> AgentResult:
        run_id = str(uuid4())
        trace: list[TraceEntry] = []
        observations: list[dict[str, Any]] = []
        reads: list[dict[str, Any]] = []
        tool_count = 0
        current_model_response: str | None = None
        clarification_guard = ClarificationGuard()
        answer_guard = FailedAnswerGuard()
        progress = ProgressTracker()

        def record(step: int, action: str, arguments: dict[str, Any], summary: str,
                   observation: dict[str, Any], tool_name: str | None = None) -> None:
            if trace:
                trace[-1].next_action = action
            trace.append(TraceEntry(
                run_id=run_id, model_mode=self.model.mode, model_name=self.model.name,
                step=step, action=action, arguments=arguments, tool_name=tool_name,
                tool_arguments=arguments if tool_name else {}, observation=observation,
                observation_summary=json.dumps(observation, ensure_ascii=False)[:700],
                decision_summary=summary, timestamp=datetime.now(timezone.utc).isoformat(),
                model_response=current_model_response,
                model_metadata=dict(getattr(self.model, "last_response_metadata", {}))))
            observations.append({"step": step, "action": action, "arguments": arguments, **observation})

        def finish(answer: str, status: str, gaps: list[str], citations: list[Citation] | None = None,
                   claims: list[ClaimEvidence] | None = None, evidence_review: dict | None = None) -> AgentResult:
            if trace:
                trace[-1].next_action = "END"
            for entry in trace:
                entry.final_status = status
            return AgentResult(answer=answer, status=status, gaps=gaps, citations=citations or [], trace=trace,
                               run_id=run_id, model_mode=self.model.mode, model_name=self.model.name,
                               knowledge_base_sha256=self.tools.store.sha256, claims=claims or [], evidence_review=evidence_review or {})

        def terminal_failure(step: int, action: str, arguments: dict, summary: str, code: str,
                             stage: str, details: list[str], layers: dict | None = None) -> AgentResult | None:
            stalled = action == "ASK_CLARIFICATION" and clarification_guard.failed(arguments, stage, details)
            repeated_answer=(action=="ANSWER" and self.config.answer_quality_mode=="enforce"
                and answer_guard.failed(arguments,stage,details,reads))
            error = {"code": code, "validation_stage": stage, "details": details,
                     "submitted_to_user": False,
                     "repair_hint": {
                         "schema_validation": "按动作协议在arguments内补齐合法claims；格式错误不代表原文缺失。",
                         "answer_claim_coverage": "核对unmapped_fragments及claim绑定，覆盖answer和reason中全部事实；保留真实问题，不要用资料不足替代映射修复。",
                         "evidence_support": "映射成功不代表引文支持。限定结论、公开标明推论或承认未知；需要时由你决定补读。",
                         "citation_integrity": "修正文档/版本/逐字片段和claim引用索引；引文必须来自本次成功READ。",
                         "answer_applicability": "引文真实不等于适用于当前用户。核对用户明确条件与操作前提，自主修正表述、补读或澄清；不要编造可用路径。",
                         "task_coverage": "核对task_coverage中的所问对象和具体属性。真实引文也可能答非所问；自主检索/补读或明确对应UNKNOWN和gaps，不要把其他属性的数字挪作答案。",
                     }[stage]}
            if layers is not None:
                error["layers"] = layers
            if stalled:
                error["repeat_control"] = "identical_failed_clarification_draft"
            if repeated_answer:
                error["repeat_control"] = "identical_failed_answer_draft"
            record(step, action, arguments, summary, {"ok": False, "error": error})
            if stalled:
                return finish("连续重复的澄清候选未通过校验，任务尚未完成。", "validation_failed",
                              ["澄清草稿未提交给用户；需修正声明映射或证据问题后再继续。"])
            if repeated_answer:
                return finish("连续重复的回答候选未通过校验，任务尚未完成。","validation_failed",
                    ["回答草稿未提交给用户；重复相同草稿没有修复声明映射或证据问题。"])
            return None

        for step in range(1, self.config.max_steps + 1):
            current_model_response = None
            context = {**request.model_dump(mode="json"), "observations": observations,
                       "budget": {"decisions_remaining": self.config.max_steps - step + 1,
                                  "tool_calls_remaining": self.config.max_tool_calls - tool_count}}
            try:
                raw = self.model.decide(context)
            except ModelError as exc:
                if str(exc)=="phase_api_budget_exhausted":
                    record(step,"MODEL_LIMIT",{},"本地模型请求预算已用尽，未发出新的HTTP请求。",
                        {"ok":False,"error":{"code":"phase_api_budget_exhausted","origin":"local_api_budget","executed":False}})
                    return finish("已达到模型请求预算上限，任务尚未完成。","max_steps_reached",["模型请求预算已用尽"])
                record(step, "MODEL_ERROR", {}, "模型接口调用失败，未产生业务动作。",
                       {"ok": False, "error": {"code": "model_failed", "message": str(exc)}})
                return finish("模型服务调用失败，任务尚未完成。", "tool_failed", [str(exc)])
            try:
                decision = parse_decision(raw)
            except (ValidationError, ValueError, TypeError) as exc:
                # Do not retain invalid raw output or arbitrary provider diagnostics.
                errors = ([{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
                          if isinstance(exc, ValidationError) else [{"type": type(exc).__name__}])
                record(step, "INVALID_DECISION", {}, "动作格式校验失败，反馈给模型重新选择。",
                       {"ok": False, "error": {"code": "invalid_decision", "validation_stage": "schema_validation", "details": errors}})
                continue
            action, args = decision.action, decision.arguments
            current_model_response = raw if isinstance(raw, str) else decision.model_dump_json()
            if action in {"SEARCH", "READ"}:
                name = "search_documents" if action == "SEARCH" else "read_document"
                if tool_count >= self.config.max_tool_calls:
                    record(step, action, args, decision.decision_summary,
                           {"ok": False, "error": {"code": "tool_call_limit", "executed": False}}, name)
                    return finish("已达到工具调用上限，任务尚未完成。", "max_steps_reached", ["工具调用预算已用尽"])
                tool_count += 1
                try:
                    output = self.tools.execute(name, args)
                    output = validate_tool_result(name, output, args, self.tools.store)
                    observation = {"ok": True, "result": output}
                    if action == "READ":
                        reads.append(output["document"])
                except ToolError as exc:
                    observation = {"ok": False, "error": {"code": exc.code, "message": str(exc),
                                                            "retryable": exc.retryable}}
                except Exception as exc:
                    observation = {"ok": False, "error": {"code": "unexpected_tool_error",
                                                            "message": type(exc).__name__, "retryable": False}}
                if self.config.answer_quality_mode=="enforce":
                    observation["progress"]=progress.observe(action,observation,
                        steps_left=self.config.max_steps-step,tools_left=self.config.max_tool_calls-tool_count)
                record(step, action, args, decision.decision_summary, observation, name)
                continue

            if action == "ASK_CLARIFICATION":
                proposed = ClarificationArgs.model_validate(args)
                answer = "\n".join(s for s in [proposed.answer, proposed.reason, proposed.question] if s)
                citations, gaps, status = proposed.citations, proposed.gaps, "needs_clarification"
                claims = proposed.claims
                segments = proposed.answer_segments
                parts = {"answer": proposed.answer, "reason": proposed.reason, "question": proposed.question}
            else:
                proposed_answer = AnswerArgs.model_validate(args)
                answer, citations, gaps, status = (proposed_answer.answer, proposed_answer.citations,
                                                   proposed_answer.gaps, proposed_answer.status)
                claims = proposed_answer.claims
                segments = proposed_answer.answer_segments
                parts = {"answer": answer}
            errors = validate_citations(citations, self.tools.store, reads, claims)
            if status == "completed" and not citations:
                errors.append("completed_requires_read_citations")
            if status == "max_steps_reached":
                errors.append("max_steps_reached_is_runtime_owned")
            if status == "validation_failed":
                errors.append("validation_failed_is_runtime_owned")
            if errors:
                stopped = terminal_failure(step, action, args, decision.decision_summary,
                                           "citation_validation_failed", "citation_integrity", errors)
                if stopped:
                    return stopped
                continue
            strict_mapping = self.config.require_claims and (status in {"completed", "insufficient_evidence"} or bool(citations))
            metadata = [{**self.tools.store.documents[citation.doc_id].model_dump(mode="json"), "quote": citation.quote}
                        for citation in citations]
            review = review_answer(answer, citations, gaps, status, claims, require_claims=strict_mapping,
                                   answer_segments=segments, parts=parts, question=request.question,
                                   citation_metadata=metadata, read_documents=reads,request=request)
            if review.errors:
                stopped = terminal_failure(step, action, args, decision.decision_summary,
                                           "evidence_support_failed", review.failure_stage, review.errors, review.public()["layers"])
                if stopped:
                    return stopped
                continue
            public_review = review.public()
            quality_mode = self.config.answer_quality_mode
            if quality_mode != "off":
                quality = review_quality(request, answer, review.claims, parts, citation_metadata=metadata, gaps=gaps, status=status,
                    support_proofs=review.support_proofs)
                if quality_mode == "enforce" and quality.errors:
                    failure_stage = "task_coverage" if quality.task_coverage.get("findings") else "answer_applicability"
                    stopped = terminal_failure(step, action, args, decision.decision_summary,
                        "answer_quality_failed", failure_stage, quality.errors,
                        {"task_applicability": quality.public(quality_mode)})
                    if stopped:
                        return stopped
                    continue
                public_review["answer_quality"] = quality.public(quality_mode)
            if quality_mode == "enforce" and action == "ASK_CLARIFICATION":
                displayed, displayed_parts, changed = clarification_delivery(parts)
                if changed:
                    # Validate the entire raw protocol first, then independently
                    # recheck delivered facts. Never discard an unsupported
                    # sentence to manufacture a passing answer.
                    checked = review_answer(displayed, citations, gaps, status, claims, require_claims=strict_mapping,
                        parts=displayed_parts, question=request.question, citation_metadata=metadata, read_documents=reads,request=request)
                    if checked.errors:
                        stopped = terminal_failure(step, action, args, decision.decision_summary,
                            "evidence_support_failed", checked.failure_stage, checked.errors, checked.public()["layers"])
                        if stopped:
                            return stopped
                        continue
                    quality_public = public_review.get("answer_quality")
                    public_review = checked.public()
                    public_review["answer_quality"] = quality_public
                    public_review["presentation"] = {"duplicate_reason_omitted": True, "delivery_revalidated": True,
                        "original_segments_validated": bool(segments),
                        "raw_answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest()}
                    answer = displayed
            record(step, action, args, decision.decision_summary,
                   {"ok": True, "result": {"status": status, "evidence_review": public_review}})
            return finish(answer, status, gaps, citations, review.claims, public_review)
        return finish("已达到最大决策步数，任务尚未完成。", "max_steps_reached", ["决策预算已用尽，尚未形成通过校验的结果"])
