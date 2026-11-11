"""Closed, auditable workflow relations, never global method exclusivity.

A described verification step supports a statement about that workflow only.
Combining it with user state requires a matching real USER assertion. Values,
availability, alternative methods and subject qualifiers are not discarded.
"""
from __future__ import annotations
import re

from .claim_mapping import compact
from .contact_state import unavailable_channel
from .schemas import QuestionInput

AUTH_STEPS=[re.compile(r"(?:通过|使用|经由)(?P<method>[^，。！？；\n]{2,24}?)(?:完成|进行)?(?P<operation>验证|核验)"),
            re.compile(r"完成(?P<method>[^，。！？；\n]{2,24}?)(?P<operation>验证|核验)")]
# A bare method is recognized only at a clause boundary and with an explicit
# 'after verification' sequence below. Negated/optional methods stay excluded.
BARE_STEP = re.compile(r"^(?P<method>[^，。！？；\n]{2,24}?)(?P<operation>验证|核验)")
NEGATED=re.compile(r"无需|不需要|不必|不得|禁止|不支持|不能|无法|不允许|未完成|未能|并非|不是|不要求|不强制")
OPTIONAL=re.compile(r"(?:验证|核验)(?:步骤|环节)?(?:为|是)?(?:可选|非必需)|(?:可|可以|允许)跳过(?:验证|核验)|"
    r"(?:验证|核验)(?:步骤|环节)?(?:可|可以)省略|(?:不需要|无需|不必|不要求|不强制)(?:进行|完成)?(?:验证|核验)|"
    r"可以选择|可选择|(?:也可(?:以)?|或(?:者)?)通过")
FLOW=r"(?:上述|该|此|所述)(?:自助|网页端?|移动端?)?流程"
DECLARATION=re.compile(r"(?:据此|基于原文)(?:只能|仅可|可以)?(?:推断|判断|推知)[:：]?"+FLOW+
    r"(?:均|都)?(?:要求|需要|需)以(?P<method>[^，。！？；\n]{2,24}?)完成(?P<operation>验证|核验)[。]?")


def method_key(text: str) -> str:
    value=compact(text)
    # The referent remains the bound contact in this workflow. Do not erase
    # 未绑定, 有效, department qualifiers or other arbitrary method modifiers.
    return "绑定手机号" if re.fullmatch(r"绑定(?:的)?(?:工作)?手机(?:号码|号)",value) else value


def described_steps(quotes: list[str]) -> list[dict]:
    result=[]
    for index,quote in enumerate(quotes):
        if OPTIONAL.search(quote):
            continue
        for clause in re.split(r"[，。！？；\n]",quote):
            patterns = AUTH_STEPS if any(pattern.search(clause) for pattern in AUTH_STEPS) else [BARE_STEP]
            for pattern in patterns:
                for match in pattern.finditer(clause):
                    if NEGATED.search(clause[max(0,match.start()-8):match.start()] + match['method']):
                        continue
                    if clause[match.end():].startswith("码"):
                        continue
                    tail=clause[match.end():]
                    before=clause[:match.start()]
                    # Authenticate a prerequisite in the described sequence,
                    # not a bare mention that a verification method exists.
                    if not re.match(r"(?:身份)?后",tail) and not re.search(r"(?:必须|需要|应当|须|需)(?:先)?$",before):
                        continue
                    method = match['method']
                    if pattern is BARE_STEP:
                        # Prefixed forms have their own parser; do not turn
                        # '通过有效证件' into a different bare method.
                        if re.match(r"通过|使用|经由|完成|可|必须|需要|应当|须|需", method):
                            continue
                    result.append({"quote_index":index,"method":method_key(method),
                        "operation":match["operation"],"source_span":match.group()})
    return result


def matching_steps(quotes: list[str],method: str,operation: str) -> list[dict]:
    choices=[row for row in described_steps(quotes) if row["operation"]==operation]
    # Explicit alternatives cannot be collapsed into one required method.
    return choices if choices and all(row["method"]==method for row in choices) else []


def workflow_inference(text: str,quotes: list[str],request: QuestionInput | None = None) -> dict | None:
    value=text.strip()
    declaration=DECLARATION.fullmatch(value)
    if declaration:
        method=method_key(declaration["method"])
        steps=matching_steps(quotes,method,declaration["operation"])
        if steps:
            return {"rule":"within_described_workflow","method":method,"source_steps":steps,
                "limit":"Only the referenced workflow, not all possible recovery methods or global exclusivity."}
        return None
    if request is None:
        return None
    state=unavailable_channel(request)
    if not state or state.get("state_scope")!="actual_user_statement":
        return None
    parts=[part.strip() for part in re.split(r"[，,。！？；\n]+",value) if part.strip()]
    if len(parts)!=3:
        return None
    user_fact=re.fullmatch(r"(?:你的|您的|用户提供的)?(?:原|当前)?绑定(?:的)?(?:工作)?手机(?:号码|号)(?:已(?:经)?)?"
        r"(?P<state>停用|停机|失效|注销|丢失|不可用|无法使用|不能使用)",parts[0])
    premise=re.fullmatch(r"(?:因此|所以|据此)"+FLOW+r"(?:要求|需要|需)?(?P<method>.+?)(?P<operation>验证|核验)(?:的)?前提(?:不满足|未满足)",parts[1])
    consequence=re.fullmatch(r"(?:不能|无法)直接(?:按(?:它|该流程|此流程|上述流程)操作|使用(?:该流程|此流程|上述流程))",parts[2])
    if not user_fact or not premise or not consequence:
        return None
    if user_fact["state"] not in state["quote"] and user_fact["state"] not in {"不可用","无法使用","不能使用"}:
        return None
    method=method_key(premise["method"])
    if method!="绑定手机号":
        return None
    steps=matching_steps(quotes,method,premise["operation"])
    if not steps:
        return None
    return {"rule":"user_condition_excludes_this_workflow","method":method,"source_steps":steps,
        "user_evidence":state,"limit":"Excludes the described bound-contact workflow only; no assertion that all recovery is impossible."}
