"""Bound verification-contact state from explicit user messages only.

Availability and binding are separate. Other contacts do not replace the bound
one without an explicit, completed binding statement. This is bounded Chinese
state parsing, not identity verification or a general coreference resolver.
"""
from __future__ import annotations
import re

from .schemas import QuestionInput

CHANNEL = r"(?:手机号码|手机号|(?<!工单)(?<!订单)(?<!资产)(?<!设备)(?<!单据)(?<!票据)(?<!房间)(?<!发票)号码)"
CONTACT = re.compile(CHANNEL + r"|(?:备用|新|旧)号")
NEGATIVE = r"无法接收(?:短信|验证码)|不能接收(?:短信|验证码)|收不到(?:短信|验证码)|无法使用|不能使用|不可用|还没恢复|没有恢复|未恢复|停用|停机|失效|注销|丢失"
STATE = re.compile(r"(?P<bad>" + NEGATIVE + r")|(?P<good>恢复(?:正常|可用)?|可用|能(?:正常)?(?:接收|收到)(?:短信|验证码)?)")
OTHER = re.compile(r"(新|备用|另一个|其他|朋友|同事|家人|妈妈|父亲)(?:的)?$")
BIND_DONE = re.compile(r"(?:已(?:经)?|成功).{0,3}(?:绑定|换绑)|(?:绑定|换绑).{0,4}(?:成功|完成|好了)")
HYPOTHETICAL = re.compile(r"假如|如果|假设|万一|若")


def _scan(messages: list[tuple[int, str]], *, hypothesis_only: bool = False) -> tuple[dict | None, bool]:
    """Track the bound contact; hypothetical premises use a separate scan."""
    bound = None
    active_alternative = None
    touched = False
    for index, text in messages:
        for clause in re.split(r"[，,。！？；\n]", text):
            hypothetical = bool(HYPOTHETICAL.search(clause))
            if hypothesis_only and not hypothetical:
                continue
            contacts = list(CONTACT.finditer(clause))
            for position, contact in enumerate(contacts):
                prefix = clause[:contact.start()][-14:]
                label = contact.group()
                other = OTHER.search(prefix)
                if label in {"新号", "备用号"}:
                    key = label[:-1]
                else:
                    key = other.group(1) if other else None
                former = bool(re.search(r"旧(?:的)?$|原(?:来)?(?:绑定的?)?$", prefix) or label == "旧号")
                end = contacts[position + 1].start() if position + 1 < len(contacts) else len(clause)
                tail = clause[contact.end():end][:48]
                if HYPOTHETICAL.search(prefix) and not hypothesis_only:
                    continue
                bind = BIND_DONE.search(tail)
                if key and bind and not re.search(r"未|没|不|想|准备|打算|计划", tail[:bind.start()]):
                    active_alternative, bound = key, None
                    touched = True
                if key and key != active_alternative:
                    continue
                if former and active_alternative is not None:
                    continue
                for state in STATE.finditer(tail):
                    before = tail[:state.start()]
                    if HYPOTHETICAL.search(before) and not hypothesis_only:
                        continue
                    if re.search(r"不|没|未|并非", before[-7:]) or re.search(r"不是|并非|没有", prefix):
                        if state.group("bad"):
                            bound = None  # Withdraw the prior assertion, not proof of successful verification.
                            touched = True
                        continue
                    touched = True
                    if state.group("bad"):
                        bound = {"state": "unavailable", "message_index": index, "role": "user",
                                 "entity_role": "bound_contact", "contact_reference": label,
                                 "quote": clause[contact.start():contact.end() + state.end()],
                                 "binding_basis": "completed_user_rebinding" if active_alternative else "primary_user_contact",
                                 "state_scope": "question_hypothesis" if hypothesis_only else "actual_user_statement"}
                    else:
                        bound = None
    return bound, touched


def unavailable_channel(request: QuestionInput) -> dict | None:
    """Actual user assertions only; a hypothetical never updates real state."""
    messages = [(index, item.content) for index, item in enumerate(request.history) if item.role == "user"]
    messages.append((len(request.history), request.question))
    return _scan(messages)[0]


def question_channel_condition(request: QuestionInput) -> tuple[bool, dict | None]:
    """Return a premise for this answer, separately from real contact state.

    A recognized hypothetical availability can override actual availability for
    answering that hypothetical, but does not establish completed real rebinding.
    Other/unbound contacts still cannot replace the primary bound contact.
    """
    state, touched = _scan([(len(request.history), request.question)], hypothesis_only=True)
    return touched, state
