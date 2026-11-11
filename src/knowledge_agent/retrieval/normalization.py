"""Inspectable business concepts, separate from policy facts and the frozen tokenizer.

Aliases describe *what to look for*, never an answer, precedence, or document ID.
The original query and its limiting words remain available to every scoring route.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


# Canonical vocabulary is present in the synthetic corpus. A concept can only
# expand a query when the selected corpus contains its source-side anchor.
CANONICAL = {
    "expense": "费用报销 报销申请 提交期限",
    "hardware": "办公设备 故障 设备报修",
    "mail": "邮箱 密码重置 找回密码",
    "leave": "年假 请假申请 直属主管审批",
    "vpn": "VPN 远程访问 内网 权限",
}
SOURCE_ANCHORS = {
    "expense": r"报销|报账",
    "hardware": r"报修|维修|修理|设备.{0,8}故障|故障.{0,8}设备",
    "mail": r"邮箱|邮件.{0,8}(?:密码|账户|账号)",
    "leave": r"年假|带薪假|请假",
    "vpn": r"vpn|远程.{0,8}(?:访问|授权)|内网",
}
# These are retained as text, not erased as stopwords or used to decide truth.
LIMITERS = re.compile(
    r"立即|马上|最长|最迟|最晚|之前|之后|以前|以后|后才能|才能|不支持|暂不支持|"
    r"仅限|仅支持|只有|全体员工|正式员工|外包员工|[\u4e00-\u9fff]{2,6}部门|"
    r"不能|无需|免去|跳过|未开通|尚未|不可以|不允许|不用|还没|未审批"
)


def contains(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.IGNORECASE))


def source_topics(text: str) -> set[str]:
    return {name for name, pattern in SOURCE_ANCHORS.items() if contains(pattern, text)}


@dataclass(frozen=True)
class QueryPlan:
    original: str
    topics: tuple[str, ...]
    canonical_queries: tuple[str, ...]
    attributes: tuple[str, ...]
    qualifiers: tuple[str, ...]
    matched_expressions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_query(query: str, available_topics: set[str]) -> QueryPlan:
    topics: set[str] = set()
    matched: list[str] = []

    def activate(name: str, pattern: str) -> None:
        found = re.search(pattern, query, re.IGNORECASE)
        if found and name in available_topics:
            topics.add(name)
            matched.append(found.group())

    activate("expense", r"报销|报账|交票|交发票|垫付")
    # A payment/receipt business context is required; '先' or '窗口' alone
    # must not cause an expense expansion.
    if contains(r"(?:差旅|交通|费用|票据|发票|凭证|单据|钱)", query):
        activate("expense", r"先.{0,5}(?:付|支付|出)|垫.{0,6}(?:钱|费)|先行支付")
    if contains(r"票据|发票|凭证|单据", query):
        activate("expense", r"递交|递送|提交|上交|报送|交给|补交|最[迟晚长]|截止|时限|期限|窗口|何时|多久")

    activate("vpn", r"vpn|内网|远程.{0,8}(?:访问|办公|权限|授权)")
    activate("mail", r"邮箱|邮件.{0,12}(?:账号|账户|密码|登录|系统)|(?:密码|账号).{0,8}(?:恢复|重置|找回)")
    activate("leave", r"年假|休假|带薪假|请假")
    if "vpn" not in topics:
        activate("hardware", r"报修|维修|修理|故障")
        if contains(r"机器|设备|电脑|笔记本|办公终端", query):
            activate("hardware", r"无法启动|不能启动|开不了机|不能开机|坏了|无法开机")

    attributes = []
    # Attributes are matched separately from topic. A document about repairing
    # a computer is not automatically evidence about its cost or response SLA.
    for name, pattern in {
        "money": r"金额|额度|多少钱|多少元|收费|价格|费用.{0,3}封顶|成本",
        "password_strength": r"密码.{0,8}(?:长度|多长|位数|几位|字符|复杂度)",
        "cashout": r"折现|折.{0,2}现金|换.{0,2}现金",
        "phone_unavailable": r"(?:绑定|原绑|手机号|号码).{0,10}(?:失效|停用|注销|不可用)|换号",
    }.items():
        if contains(pattern, query):
            attributes.append(name)
    if "hardware" in topics and contains(r"多久|多长|几.{0,2}[天时]|多少.{0,2}[天时]", query):
        if contains(r"响应|处理|受理|修好|维修|报修", query):
            attributes.append("service_time")
    if "mail" in topics and contains(r"网页|网页版|网页端|网页登录", query):
        attributes.append("web_channel")
    # Restrictive predicates and time values are never converted into an
    # applicability/priority decision. Both conflicting values stay eligible.
    qualifiers = tuple(dict.fromkeys(match.group() for match in LIMITERS.finditer(query)))
    canonical = []
    for name in sorted(topics):
        extra = ""
        if name == "mail" and "phone_unavailable" in attributes:
            extra = " 绑定手机号失效 人工身份核验"
        elif name == "mail" and contains(r"移动端|手机|企业微信|掌上", query):
            extra = " 移动端 企业微信"
        if name == "mail" and "web_channel" in attributes:
            extra += " 网页端 登录页"
        canonical.append(CANONICAL[name] + extra + " " + " ".join(qualifiers))
    return QueryPlan(query, tuple(sorted(topics)), tuple(canonical), tuple(attributes), qualifiers, tuple(matched))


def missing_attributes(attributes: tuple[str, ...], source: str) -> list[str]:
    """Necessary lexical evidence of a requested attribute, not entailment.

    This deliberately does not infer a cost from a time value or an operation
    channel from the word 'phone' in an identity-verification step.
    """
    evidence_patterns = {
        "money": r"金额|额度|元|人民币|收费|价格|免费|费用.{0,4}(?:上限|封顶)",
        "password_strength": r"密码.{0,12}(?:长度|位数|字符|复杂度)|[0-9一二三四五六七八九十]+.{0,2}位",
        "cashout": r"折现|折.{0,2}现金|换.{0,2}现金|现金补偿",
        "phone_unavailable": r"(?:手机|绑定|号码).{0,10}(?:失效|停用|注销|不可用)",
        "web_channel": r"网页|登录页|网站",
        "service_time": r"(?:[0-9一二三四五六七八九十]+.{0,3}(?:小时|分钟|工作日|日|天)).{0,8}(?:响应|受理|处理|修复|修好)|(?:响应|受理|处理|修复|修好).{0,8}(?:小时|分钟|工作日|日内|天内)",
    }
    return [name for name in attributes if not contains(evidence_patterns[name], source)]
