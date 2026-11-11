"""Necessary question/topic/attribute checks after evidence authentication.

No document IDs, answers, ranks or model actions are encoded. Recognition is
bounded and reported explicitly; lexical matching is not general entailment.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import re

from .schemas import ClaimEvidence, QuestionInput
from .claim_mapping import canonical

TOPICS = {
    "expense": r"报销|报账|垫付|垫款|交票|路费|票据|凭证|费用.{0,8}(?:提交|申请)",
    "hardware": r"电脑|笔记本|设备|报修|维修|修理|质保|保修",
    "mail": r"邮箱|邮件|密码|口令|重置|找回",
    "leave": r"年假|请假|休假|带薪假",
    "vpn": r"vpn|内网|远程.{0,8}(?:访问|权限)",
    "bonus": r"年终奖|年度奖金|年终奖金",
    "promotion": r"晋升|升职",
    "salary": r"调薪|涨薪|加薪|薪酬调整",
    "benefits": r"福利|体检|节日礼品",
    "subsidy": r"补贴|津贴|补助",
}
NUMBER = r"[0-9零一二两三四五六七八九十百千万半]+(?:\.[0-9]+)?"
DURATION = NUMBER + r"\s*(?:个)?(?:自然日|工作日|个月|小时|分钟|天|日|月|年)"
UNKNOWN_MARKER = re.compile(r"未明确|未规定|未提供|未说明|没有说明|尚未确定|待确认|不清楚")
ANCHORS = {
    "warranty": r"保修|质保",
    "rotation": r"更换|修改|更新|变更|轮换|周期|频率|间隔",
    "money": r"金额|多少钱|多少元|费用|价格|收费|奖金|补贴|津贴|元",
    "price": r"费用|价格|收费|多少钱|多少元|成本|花费",
    "effective": r"生效|有效|无效|时点",
    "entry": r"入口|哪里|在哪|何处|门户|服务台|网址|点击|工单|提交",
    "deadline": r"期限|时限|最晚|最迟|提交|交票|申请|自然日",
    "mobile": r"移动端|手机上|手机端|企业微信|手机操作",
    "event_time": r"何时|什么时候|时间|日期|哪天|几点|生效",
    "responsibility": r"负责|责任|主体|部门|负责人|催办|跟进|处理人|归谁|由谁",
}


def topics(text: str) -> set[str]:
    return {name for name, pattern in TOPICS.items() if re.search(pattern, text, re.IGNORECASE)}


@dataclass(frozen=True)
class Target:
    topic: str | None
    attribute: str | None
    request_text: str


def attributes(clause: str, context: str) -> list[str]:
    found = []
    if re.search(r"谁|哪个部门|哪一部门|哪个团队|哪个负责人|责任(?:人|主体|部门)|负责(?:人|部门)", clause):
        found.append("responsibility")
    if re.search(r"保修|质保", clause):
        found.append("warranty")
    if re.search(r"生效|有效期|是否有效", clause):
        found.append("effective")
    frequency = re.search(r"多久|多长|几天|多少天|周期|频率|间隔|每隔|一次", clause)
    if re.search(r"密码|口令", clause + context) and re.search(r"更换|修改|更新|轮换|变更", clause) and frequency:
        if "effective" not in found or re.search(r"周期|频率|间隔|每隔", clause):
            found.append("rotation")
    if re.search(r"多少钱|多少元|金额|价格|收费|花多少|奖金.{0,6}多少|费用(?:是|为|有)?多少|多少费用|费用$", clause) and not re.search(r"费用发生", clause):
        found.append("price" if re.search(r"价格|收费|花|维修.{0,8}(?:多少钱|费用)", clause) else "money")
    if re.search(r"在哪里|哪里|在哪|何处|入口", clause):
        found.append("entry")
    if re.search(r"手机|移动端|企业微信", clause) and re.search(r"操作|重置|可以|能", clause):
        if not re.search(r"手机号|手机号码", clause):
            found.append("mobile")
    if "expense" in topics(clause + context) and re.search(r"期限|时限|最晚|最迟|几天|多久|多少.{0,3}日", clause):
        found.append("deadline")
    if not found and re.search(r"何时|什么时候|哪(?:天|一天|个月)|时间|日期|几点", clause):
        found.append("event_time")
    return list(dict.fromkeys(found))


def targets_for(request: QuestionInput) -> list[Target]:
    focus, context = set(), ""
    for message in reversed(request.history):
        if message.role == "user" and topics(message.content):
            focus, context = topics(message.content), message.content
            break
    result = []
    for clause in re.split(r"[，,；;？?！\n]+|以及|还有", request.question):
        if not clause.strip():
            continue
        explicit = topics(clause)
        if explicit:
            focus = explicit
        fields = attributes(clause, context if not explicit else clause)
        result += [Target(topic, field, clause) for topic in sorted(focus) or [None] for field in fields or [None]]
        context = clause
    # A leading subject statement is covered by the subsequent specific target.
    return list(dict.fromkeys(target for target in result if target.attribute is not None
        or not any(other.topic == target.topic and other.attribute is not None for other in result)))


def value_present(attribute: str, text: str) -> bool:
    for clause in re.split(r"[。！？；，,\n]+|但是|但|而且|并且|同时|且", text):
        if attribute == "responsibility":
            # A submission location or the employee submitting an application
            # is not the actor responsible for handling it. Require an explicit
            # assignment and keep a narrow, auditable responsibility predicate.
            if UNKNOWN_MARKER.search(clause) or re.search(r"无人|未指定|未分配|不负责|不由", clause):
                continue
            if re.search(r"(?:由|交由)[^，。；]{1,20}?(?:负责(?:催办|跟进|处理|审核|审批|维修|报修)?|催办|跟进|处理|审核|审批)", clause):
                return True
            if re.search(r"[^，。；]{2,20}(?:负责|承担).{0,16}(?:催办|跟进|处理|审核|审批|维修|报修|责任)", clause):
                return True
            if re.search(r"(?:负责人|责任部门|责任主体|处理人)(?:为|是|：|:)[^，。；]{2,20}", clause):
                return True
        elif attribute == "warranty":
            if re.search(r"(?:保修|质保)(?:期|期限|时长)?\s*(?:为|是|有|可达|：|:)?\s*(?:"+DURATION+r"|终身|永久)", clause):
                return True
            if re.search(r"(?:终身|永久|"+DURATION+r")(?:的|免费)?(?:保修|质保)", clause):
                return True
            if re.search(r"(?:不提供|不支持|不包含|没有).{0,12}(?:保修|质保)(?:服务|保障)?$", clause):
                return True
        elif attribute == "rotation":
            if re.search(r"密码|口令", clause) and re.search(r"更换|修改|更新|轮换|变更", clause):
                if (re.search(r"(?:每隔?|间隔|周期(?:为|是|：|:)?)\s*" + DURATION, clause)
                    or re.search(DURATION + r"\s*(?:必须|应当|需要)?(?:更换|修改|更新|轮换|变更)", clause)
                    or re.search(r"无需|不必|不要求", clause)):
                    return True
        elif attribute in {"money", "price"}:
            if re.search(NUMBER + r"\s*(?:万)?(?:元|块)|人民币\s*" + NUMBER + r"|金额.{0,6}(?:不公开|不披露)", clause):
                return True
            if attribute == "price" and re.search(r"免费|不收费", clause):
                return True
        elif attribute == "effective":
            if re.search(r"生效|无效|有效期", clause) and not UNKNOWN_MARKER.search(clause):
                return True
        elif attribute == "entry":
            if re.search(r"门户|入口|服务台|网址|点击|工单|联系.{0,8}(?:部门|主管|人员)", clause):
                return True
        elif attribute == "deadline":
            if re.search(r"提交|交票|报销|申请", clause) and re.search(DURATION, clause):
                return True
        elif attribute == "mobile":
            if re.search(r"移动端|手机上|手机端|企业微信|手机操作", clause):
                return True
        elif attribute == "event_time":
            if UNKNOWN_MARKER.search(clause):
                continue
            # A document's publication/revision date does not date the policy's
            # business event. Relative, explicit event timing is still valid.
            if re.search(r"(?:制度|文档|文件).{0,8}(?:修订|版本|发布|更新|制定)(?:日期|时间)?", clause):
                continue
            calendar = re.search(r"(?:每年|每月|每周|每季度)|" + NUMBER + r"\s*(?:年|月|号|日|点)", clause)
            relative = re.search(r"(?:审批|审核|批准|提交|申请).{0,8}(?:前|后)(?:立即|开始|统一|生效|发放|执行|实施)", clause)
            if calendar or relative:
                return True
    return False


def check_task_coverage(request: QuestionInput, claims: list[ClaimEvidence], metadata: list[dict],
                        gaps: list[str], status: str) -> dict:
    if status == "tool_failed":
        return {"targets": [], "findings": [], "coverage": "not_required_for_tool_failure"}
    targets = targets_for(request)
    required_topics = {target.topic for target in targets if target.topic}
    facts, unknowns, findings = [], [], []
    for index, claim in enumerate(claims):
        if claim.kind == "UNKNOWN":
            unknowns.append(claim.text)
            continue
        source_topics = topics(claim.text)
        if not source_topics:
            # A clipped predicate inherits its actual quoted sentence, before
            # consulting a broad document title. A mixed-topic title cannot
            # turn a reimbursement amount into a bonus amount.
            for ref in claim.citation_indices:
                if ref >= len(metadata):
                    continue
                for sentence in re.split(r"[。！？；\n]+", metadata[ref].get("quote", "")):
                    value = canonical(claim.text, [metadata[ref]])
                    if value and value in canonical(sentence, [metadata[ref]]):
                        source_topics.update(topics(sentence))
            if not source_topics:
                source_topics = set().union(*(topics(str(metadata[ref].get("title", "")) + str(metadata[ref].get("scope", "")))
                                              for ref in claim.citation_indices if ref < len(metadata)))
        facts.append((index, claim.text, source_topics))
        if required_topics and source_topics and required_topics.isdisjoint(source_topics):
            findings.append({"code": "answer_topic_mismatch", "severity": "error", "claim_index": index,
                "message": "该事实虽然有真实引文，但与本次所问对象不匹配。", "repair_hint": "回答当前问题的对象；无相关证据时准确列出缺口，不以无关原文替代答案。"})
    unknown_text = " ".join(unknowns + gaps)
    unknown_topics = topics(unknown_text)
    rows = []
    for target in targets:
        related = [(index, text) for index, text, source_topics in facts if target.topic is None or target.topic in source_topics]
        known = [index for index, text in related if target.attribute is None or value_present(target.attribute, text)]
        if target.attribute == "responsibility":
            duties = re.findall(r"催办|跟进|审核|审批", target.request_text)
            if duties:
                known = [index for index, text in related if index in known and any(duty in text for duty in duties)]
        unknown_topic_ok = target.topic is None or target.topic in unknown_topics or (not unknown_topics and len(required_topics) <= 1)
        unknown_attribute_ok = target.attribute is None or bool(re.search(ANCHORS[target.attribute], unknown_text))
        unknown = bool(unknowns and gaps and unknown_topic_ok and unknown_attribute_ok)
        # A direct quotation may explicitly state that the requested field is
        # unspecified. It can support a gap without pretending to supply a value.
        documented_missing = bool(target.attribute and gaps and re.search(ANCHORS[target.attribute], " ".join(gaps))
            and any(UNKNOWN_MARKER.search(text) and re.search(ANCHORS[target.attribute], text) for _, text in related))
        classified = target.topic is not None or target.attribute is not None
        covered = bool(known or unknown or documented_missing or not classified)
        rows.append({**asdict(target), "matching_claim_indices": known,
                     "resolution": "supported_fact" if known else "explicit_unknown" if unknown else "documented_gap" if documented_missing else "unclassified" if not classified else "missing",
                     "covered": covered})
        if not covered:
            findings.append({"code": "requested_attribute_missing" if target.attribute else "requested_topic_missing", "severity": "error",
                "target": asdict(target), "message": "引用真实或主题相关，不代表已回答此对象的所问属性。",
                "repair_hint": "补充支持该具体属性的原文，或明确说明该属性未知并列出对应gaps；下一动作由模型决定。"})
    return {"targets": rows, "findings": findings,
            "coverage": "bounded_topic_attribute_checks", "limitation": "Necessary lexical/topic constraints, not general semantic entailment or unrestricted entity resolution."}
