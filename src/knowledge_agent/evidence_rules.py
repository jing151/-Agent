"""Shared bounded operator rules; no answer generation or retrieval."""
from __future__ import annotations
import re
from .claim_mapping import compact, unframe as strip_source_frame

UNCERTAINTY = re.compile(r"未说明|未明确|未标明|未注明|未写明|没有说明|没有明确|无法确定|不能确定|无法确认|不能确认|不能认定|不能仅凭|尚不明确|资料不足|未找到|没有找到|未检索到|缺少|未提供|未获得|尚未获得|尚未明确|不清楚|待确认|需要进一步确认|暂时无法|尚无")
INFERENCE_MARKER = re.compile(r"(?:据此|基于原文|仅可|只能|有限|在.{0,20}前提下).{0,12}(?:推断|推知|判断)|有限推论")
GENERALIZATION = re.compile(r"通常|一般(?:会|是|按|根据|由|需要|取决于|包含)|常见做法|行业惯例|惯常|往往")
ASSERTION = re.compile(r"必须|应当|要求|取决于|每年|每月|固定|统一|(?:按照|按|根据|由).{0,20}(?:决定|发放|评定|调整|晋升)|(?:公司|员工).{0,12}(?:享有|享受|可以|有权|实行|规定|采用|提供|包含)|(?:\d+|[一二三四五六七八九十百千万]+)(?:元|个月|天|%)")
OUTCOME = re.compile(r"生效|无效|有效")
TIMED_PAYOUT = re.compile(r"(?:\d+|[一二三四五六七八九十]+|次|第二)(?:个)?(?:工作日|天|小时|日).{0,8}(?:到账|发放)|(?:立即|马上|随即)(?:到账|发放)")
LOGIC_WORDS = set("据此 只能 推断 有限 推论 必要 条件 必要条件 前提 基于 原文 说明 因此 作为 认为 该项 现有 资料 明确 已经 当前 制度 规定".split())
DOCUMENT_SCOPE = re.compile(r"^(?:(?:该|这份|本份)(?:资料|材料|文档|制度|说明|原文)|所读(?:资料|材料|文档|原文)|制度原文|原文|本次(?:已)?(?:读(?:取)?|查阅|检索)(?:到)?(?:的)?(?:资料|文档|制度|原文)?|本次资料|已读(?:资料|文档)|现有(?:已读)?资料)")
ABSENCE = re.compile(r"未规定|未包含|未涉及|未给出|没有规定|没有包含|没有涉及|没有给出|未明确|未说明|未写明")


def scoped_absence(text: str, inherited_scope: bool = False) -> bool:
    """Document-scope absence is a boundary, not the negated policy outcome.

    A company-wide '公司未规定' is not licensed by this grammar. A previous
    document subject may govern a following comma clause within one sentence.
    """
    return bool((inherited_scope or DOCUMENT_SCOPE.match(text.strip())) and ABSENCE.search(text))


def has_uncertainty(text: str, inherited_scope: bool = False) -> bool:
    return bool(UNCERTAINTY.search(text) or scoped_absence(text,inherited_scope))


def absence_targets(text: str) -> list[str]:
    """Targets for exact counterevidence checks, never general negation removal."""
    targets=[]
    for sentence in re.split(r"[。！？；\n]+",text):
        scope=False
        for part in units(sentence):
            scope=scope or bool(DOCUMENT_SCOPE.match(part))
            match=ABSENCE.search(part)
            if scope and match:
                target=re.sub(r"^(?:关于|是否)","",part[match.end():])
                target=target.replace("是否","")
                target=re.sub(r"的规定$","",target)
                if len(normalized(target))>=6:
                    targets.append(target)
    return targets


def source_summary_supported(text: str,sources: list[str]) -> bool:
    """A one-clause source may support a bounded requirement summary.

    This does not prove arbitrary summaries or that a multi-rule document only
    contains one requirement. No event, value or negation is normalized away.
    """
    scope=DOCUMENT_SCOPE.match(text)
    if not scope:
        return False
    summary=re.fullmatch(r"(?:只|仅)(?:说明|涉及|包含)(.+?)(?:要求|流程|规定)",text[scope.end():])
    if not summary:
        return False
    event=normalized(summary[1])
    for source in sources:
        clauses=units(source)
        if len(clauses)!=1:
            continue
        value=normalized(clauses[0])
        position=value.find(event)
        before=value[:position] if position>=0 else ""
        if event and position>=0 and re.search(r"需要|必须|应当|要求|须",before) and not re.search(r"无需|不需要|不必|禁止|不得",before):
            return True
    return False


def normalized(text: str) -> str:
    return compact(text)


def units(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。！？；\n，,]+|但是|然而|不过|而且|并且|同时|因此|所以|此外|但|且|并(?!非)", text) if part.strip()]


def unframe(text: str) -> str:
    return strip_source_frame(text).strip()


def literal_support(text: str, quotes: list[str]) -> bool:
    value = normalized(unframe(text))
    return bool(value) and any(value in normalized(quote) for quote in quotes)


def drops_explicit_condition(text: str, quote: str) -> bool:
    """A predicate fragment alone cannot erase an explicit necessary condition."""
    operator = r"后才能|后方可|后才可|仅当|只有|除非"
    match = re.search(operator, quote)
    return bool(match and not re.search(operator, text)
                and normalized(text) in normalized(quote[match.end():]))


def necessity_direction_supported(text: str, quotes: list[str]) -> bool:
    relation = re.search(r"(.+?)是(.+?)的必要条件", INFERENCE_MARKER.sub("", text))
    if not relation:
        return True  # Other inference forms remain explicitly bounded/unverified.
    condition, consequence = (normalized(part) for part in relation.groups())
    for quote in quotes:
        split = re.split(r"后才能|后方可|后才可", quote, maxsplit=1)
        if len(split) == 2 and condition in normalized(split[0]) and consequence in normalized(split[1]):
            return True
    return False


def boundary_errors(answer: str, quotes: list[str], *, summary_sources: list[str] | None = None) -> list[str]:
    """Catch known operator-strength errors independent of question/doc IDs."""
    errors = []
    scoped_parts=[]
    for sentence in re.split(r"[。！？；\n]+",answer):
        scope=False
        for part in units(sentence):
            scope=scope or bool(DOCUMENT_SCOPE.match(part))
            scoped_parts.append((part,scope))
    for part,scope in scoped_parts:
        part = unframe(part)
        if has_uncertainty(part,scope) or part.startswith(("建议", "请确认", "请补充")):
            continue
        if source_summary_supported(part,summary_sources or quotes):
            continue
        if literal_support(part, quotes):
            matched = [quote for quote in quotes if literal_support(part, [quote])]
            if all(drops_explicit_condition(part, quote) for quote in matched):
                errors.append("explicit_condition_omitted")
            continue
        if OUTCOME.search(part):
            # A workflow requirement cannot license an effectiveness/invalidity fact.
            outcome_sources = [quote for quote in quotes if OUTCOME.search(quote)]
            if not outcome_sources:
                errors.append("effectivity_not_established_by_citations")
            elif re.search(r"并非|不会|不立即|无效", part) and not any(re.search(r"并非|不会|不立即|无效", quote) for quote in outcome_sources):
                errors.append("effectivity_polarity_not_supported")
        if TIMED_PAYOUT.search(part):
            errors.append("payout_time_not_established_by_citations")
        if GENERALIZATION.search(part):
            errors.append("unsupported_generalization")
        if not quotes and ASSERTION.search(part):
            errors.append("uncited_enterprise_fact")
    return list(dict.fromkeys(errors))
