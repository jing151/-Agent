"""OFFLINE ONLY: deterministic fixture policy, NOT evidence of real LLM reasoning.

Consumes actual observations and quotes actual reads so document mutation can be tested.
Its limited natural-day conflict detector is a test fixture, not the real-mode policy.
"""
from __future__ import annotations

import re
from typing import Any


def decision(action: str, arguments: dict[str, Any], summary: str) -> dict[str, Any]:
    return {"action": action, "arguments": arguments, "decision_summary": summary}


class MockLLM:
    mode = "mock"
    name = "offline-observation-fixture-v1"

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        result = self._decide(context)
        args = result['arguments']
        if result['action'] in {'ANSWER', 'ASK_CLARIFICATION'} and args.get('status') != 'tool_failed':
            # Offline fixture only: classify its own verbatim quotes and explicit
            # gap text. This is never applied to real-model output or test actions.
            args['claims'] = [dict(text=item['quote'],kind='DIRECT',citation_indices=[index])
                              for index,item in enumerate(args.get('citations', []))]
            if result['action'] == 'ASK_CLARIFICATION':
                args['claims'].append(dict(text=args['reason'],kind='UNKNOWN',citation_indices=[]))
            elif args.get('status') == 'insufficient_evidence':
                args['claims'].append(dict(text=args['answer'],kind='UNKNOWN',citation_indices=[]))
                args['gaps'].append('本次问题所需的原文依据：' + context['question'])
        return result

    def _decide(self, context: dict[str, Any]) -> dict[str, Any]:
        observations = context["observations"]
        query = " ".join([m["content"] for m in context["history"] if m["role"] == "user"]
                         + [context["question"]])
        if observations and not observations[-1]["ok"]:
            last = observations[-1]
            error = last["error"]
            failures = [o for o in observations if not o["ok"]]
            if error.get("retryable") and len(failures) == 1:
                return decision(last["action"], last["arguments"], "离线测试策略：观察到可恢复错误，选择再调用一次工具。")
            return decision("ANSWER", {"answer": "工具执行失败，当前无法获得可靠资料。", "citations": [],
                                       "gaps": [error["code"]], "status": "tool_failed"},
                            "离线测试策略：观察到不可恢复错误，停止。")
        searches = [o for o in observations if o["action"] == "SEARCH" and o["ok"]]
        if not searches:
            return decision("SEARCH", {"query": query, "top_k": 3}, "离线测试策略：结合历史问题构造检索输入。")
        hits = searches[-1]["result"]["results"]
        if not hits:
            return decision("ANSWER", {"answer": "当前知识库未检索到支持该问题的可靠资料，无法确认答案。",
                                       "citations": [], "gaps": ["缺少该问题对应的制度或 FAQ"],
                                       "status": "insufficient_evidence"}, "离线测试策略：无命中时报告资料缺口。")
        documents = [o["result"]["document"] for o in observations if o["action"] == "READ" and o["ok"]]
        read_ids = {doc["doc_id"] for doc in documents}
        for hit in hits:
            if hit["doc_id"] not in read_ids:
                return decision("READ", {"doc_id": hit["doc_id"], "section_id": None},
                                "离线测试策略：读取实际检索到且尚未阅读的候选原文。")
        citations = [{"doc_id": d["doc_id"], "version_date": d["version_date"], "quote": d["content"]}
                     for d in documents]
        rules = [(doc, re.findall(r"(\d+)\s*个自然日", doc["content"])) for doc in documents]
        deadlines = {value for _, values in rules for value in values}
        unresolved = [doc for doc, values in rules if values and doc["scope"] is None
                      and doc["priority"] is None and not doc["supersedes"]]
        quoted = "\n".join(f"依据 {d['doc_id']}（{d['version_date']}）：{d['content']}" for d in documents)
        if len(deadlines) > 1 and len(unresolved) > 1:
            return decision("ASK_CLARIFICATION", {
                "answer": quoted,
                "question": "请确认你适用哪份报销制度，或提供负责部门确认的适用范围和优先级？",
                "reason": "现有资料无法确认两份冲突制度的适用范围、优先级和替代关系；不能仅凭版本日期选择期限。",
                "citations": citations, "gaps": ["适用范围", "制度优先级", "明确替代关系"]},
                "离线测试策略：读取结果存在不同自然日期限且适用关系不明，输出澄清。")
        return decision("ANSWER", {"answer": quoted, "citations": citations, "gaps": [], "status": "completed"},
                        "离线测试策略：直接引用本次成功读取的原文，不预置文档答案。")
