"""Task applicability is independent of citation truth.

These closed checks detect explicit unavailable verification channels and global
absence overclaims. They are not a general semantic judge, and never generate
an answer, pick a document, retry a tool, or treat similarity as entailment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .schemas import ClaimEvidence, QuestionInput
from .contact_state import CHANNEL, question_channel_condition, unavailable_channel
from .task_coverage import check_task_coverage
from .claim_mapping import compact
from .evidence_rules import has_uncertainty

REQUIRES_CHANNEL = re.compile(CHANNEL + r"[^，。！？；\n]{0,14}?(?:验证|验证码|接收短信)")
CONDITIONAL = re.compile(r"(?:如果|若|仅在|只有|仅当)[^。！？；\n]{0,16}?" + CHANNEL + r"(?P<link>[^。！？；\n]{0,10}?)(?:可用|恢复|能正常接收|未停用|未失效)")
GLOBAL_ABSENCE = re.compile(r"(?:全公司|全企业|整个知识库|所有制度)[^，。！？；\n]{0,12}(?:没有|不存在|均无|从未)")


@dataclass
class QualityReview:
    findings: list[dict] = field(default_factory=list)
    task_coverage: dict = field(default_factory=dict)
    contextualized_background: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return list(dict.fromkeys(item["code"] for item in self.findings if item["severity"] == "error"))

    def public(self, mode: str) -> dict:
        return {"mode": mode, "gate": "enforced" if mode == "enforce" else "observation_only",
                "findings": self.findings, "error_codes": self.errors,
                "human_review_required": bool(self.findings),
                "task_coverage": self.task_coverage,
                "contextualized_background":self.contextualized_background,
                "scope": "bounded contact binding, topic/attribute and global absence checks; not general entailment"}


def duplicate_reason(parts: dict[str, str]) -> bool:
    """Only a complete, byte-equal reason at a sentence boundary is redundant.

    No fuzzy matching, punctuation stripping, value conversion, or negation
    deletion: a similar but materially different sentence must remain.
    """
    reason, answer = parts.get("reason", "").strip(), parts.get("answer", "")
    if not reason:
        return False
    return any((match.start() == 0 or answer[match.start() - 1] in "。！？；\n")
               and (match.end() == len(answer) or answer[match.end() - 1] in "。！？；\n")
               for match in re.finditer(re.escape(reason), answer))


def review_quality(request: QuestionInput, answer: str, claims: list[ClaimEvidence],
                   parts: dict[str, str], *, citation_metadata: list[dict] | None = None,
                   gaps: list[str] | None = None, status: str = "completed", support_proofs: list[dict] | None = None) -> QualityReview:
    result = QualityReview()
    if status=='needs_clarification':
        separate=parts.get('question','').strip()
        explicit=bool(separate and len(separate)>=4 and re.search(r"请|是否|哪|谁|何时|如何|怎样|[？?]",separate))
        if not explicit:
            explicit=bool(re.search(r"(?:^|[。！？；\n])\s*(?:请(?:确认|提供|补充|告知|核实|问)|能否|可否)[^。！？；\n]{2,}",answer)
                or re.search(r"[^。！？；\n]{0,80}(?:哪份|哪个|哪项|是否|谁|何时|如何|怎样)[^。！？；\n]{0,80}[？?]",answer))
        if not explicit:
            result.findings.append(dict(code='clarification_request_missing',severity='error',
                message='needs_clarification必须在交付文本中提出明确的确认问题；仅列未知或设置状态不足以请求澄清。',
                repair_hint='保留证据与缺口，并实际询问需确认的适用范围或其他缺失信息；不得通过换成无问题的ANSWER绕过。'))
    hypothetical, condition = question_channel_condition(request)
    unavailable = condition if hypothetical else unavailable_channel(request)
    if unavailable:
        for match in re.finditer(r"[^。！？；\n]+", answer):
            clause = match.group()
            requirement = REQUIRES_CHANNEL.search(clause)
            conditional = any(not re.search(r"不|没|未|无法", item.group("link")) for item in CONDITIONAL.finditer(clause))
            if not requirement or conditional:
                continue
            if any(claim.kind=="UNKNOWN" and compact(clause)==compact(claim.text) and has_uncertainty(clause)
                   and not re.search(r"请|建议|应当|你可以|您可以|直接按|(?:可|可以|能够)通过",clause) for claim in claims):
                # An explicitly uncertain verification question is not a
                # recommendation. Positive instructions remain checked.
                continue
            contextualized=False
            for proof in support_proofs or []:
                if proof.get("rule")!="user_condition_excludes_this_workflow":
                    continue
                inferred=claims[proof["claim_index"]]
                # The validated public inference itself explains why the step
                # cannot be followed; it is not an instruction to follow it.
                if compact(clause)==compact(inferred.text):
                    contextualized=True
                positive_refs={proof["citation_indices"][item["quote_index"]] for item in proof["source_steps"]}
                for claim in claims:
                    if (claim.kind=="DIRECT" and positive_refs.intersection(claim.citation_indices)
                        and compact(clause)==compact(claim.text)
                        and not re.search(r"请|建议|你可以|您可以|你应|您应|现在|直接",clause)):
                        contextualized=True
                if contextualized:
                    result.contextualized_background.append({"answer_span":[match.start(),match.end()],
                        "claim_index":proof["claim_index"],"basis":"validated_user_condition_and_workflow_inference"})
                    break
            if contextualized:
                continue
            before = clause[max(0, requirement.start() - 8):requirement.start()]
            if re.search(r"无需|不需要|不用|不必|不通过|不要", before):
                continue
            result.findings.append({"code": "unavailable_verification_channel", "severity": "error",
                "answer_span": [match.start(), match.end()], "input_evidence": unavailable,
                "message": ("当前问题假设该验证联系方式不可用" if hypothetical else "用户已明确该验证联系方式不可用")
                    + "，不能把依赖它的通用流程直接作为当前操作建议。",
                "repair_hint": "保留引文的适用前提；区分通用事实与当前可执行建议，或仅回答已有支持的可行路径。不要补造绕过核验的方法。"})
    for index, claim in enumerate(claims):
        if claim.kind == "UNKNOWN" and GLOBAL_ABSENCE.search(claim.text):
            result.findings.append({"code": "unknown_scope_overclaim", "severity": "error", "claim_index": index,
                "message": "本次未读到或未检索到资料，不能证明全企业或全部制度不存在规则。",
                "repair_hint": "把资料缺口限定于本次检索和实际已读材料。"})
    if duplicate_reason(parts):
        result.findings.append({"code": "duplicate_clarification_reason", "severity": "info",
                                "message": "reason完整重复了answer中已有的一句或一段；可在完成证据校验后省略重复展示。"})
    if citation_metadata is not None:
        result.task_coverage = check_task_coverage(request, claims, citation_metadata, gaps or [], status)
        result.findings.extend(result.task_coverage["findings"])
    return result


def clarification_delivery(parts: dict[str, str]) -> tuple[str, dict[str, str], bool]:
    delivered = dict(parts)
    changed = duplicate_reason(parts)
    if changed:
        delivered["reason"] = ""
    return "\n".join(value for value in delivered.values() if value), delivered, changed
