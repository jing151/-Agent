"""Deterministic answer/claim alignment, independent of evidential truth.

Only punctuation, checked source labels and a small set of grammatical forms
are normalized. No embedding/fuzzy threshold or declared ID proves support.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata

from .schemas import AnswerSegment, ClaimEvidence


def compact(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    # Keep numeric operators; 1.5, 15, -15 and 15% must never collapse.
    text = re.sub(r"(?<=\d)\.(?=\d)", "decimalpoint", text)
    text = text.replace("%", "percentoperator").replace("-", "minusoperator")
    for operator, marker in [("<=", "lessequaloperator"), (">=", "greaterequaloperator"),
                             ("≤", "lessequaloperator"), ("≥", "greaterequaloperator"),
                             ("<", "lessoperator"), (">", "greateroperator"),
                             ("+", "plusoperator"), ("=", "equaloperator")]:
        text = text.replace(operator, marker)
    return re.sub(r"[\W_]+", "", text)


def word_forms(text: str) -> str:
    """Closed grammatical variants; scope, negation, values and 后才能 stay."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"必须经(?!过)", "必须经过", text)
    text = re.sub(r"(?<!必)须经(?:过)?|需经(?:过)?|需要经(?!过)", "需要经过", text)
    return text


def source_labels(metadata: list[dict]) -> set[str]:
    labels = set()
    for item in metadata:
        title = item.get("title", "")
        labels.update(compact(value) for value in [title, item.get("doc_id", "")] if value)
        match = re.search(r"(制度|规则|资料|FAQ)\s*([A-Za-z0-9_-]+)$", title, re.IGNORECASE)
        if match:
            labels.add(compact(match.group()))
    return labels


def unframe(text: str, metadata: list[dict] | None = None) -> str:
    labels = source_labels(metadata or [])
    # A title introduction is presentation; a false title is not silently erased
    # when canonical READ metadata is available.
    def title_frame(match):
        return "" if not metadata or compact(match.group(1)) in labels else match.group()
    text = re.sub(r"(?:依据|根据)\s*《([^》]+)》[，,:：]?", title_frame, text)
    def label_frame(match):
        return "" if compact(match.group(1)) in labels else match.group()
    text = re.sub(r"((?:制度|文档|资料)\s*[A-Za-z0-9_-]+)\s*(?:规定|写明|说明|的)?\s*[:：，,]?", label_frame, text)
    text = re.sub(r"(?:依据|根据)\s*([A-Za-z][\w:-]*)\s*[（(][^）)]*[）)]\s*[:：]",
                  lambda m: "" if not metadata or compact(m.group(1)) in labels else m.group(), text)
    text = re.sub(r"(?:依据|根据)(?:该|上述|本)(?:制度|文档|资料)[，,:：]?", "", text)
    return text


def canonical(text: str, metadata: list[dict] | None = None, *, framing: bool = True) -> str:
    if framing:
        text = unframe(text, metadata)
        text = re.sub(r"(?:目前可确定的是|目前可确定|可以确认的是)[:：]?", "", text)
        text = re.sub(r"(?:本次已读取的(?:制度|文档|资料)|本次查阅的资料|本次已查资料|当前已读取的资料|该文档|该资料|该制度|现有已读资料|已读资料|现有资料)", "本次资料", text)
        text = re.sub(r"本次查阅(?=无法|不能|未|尚未|没有|缺少)","本次资料",text)
        text = re.sub(r"(?:因此|所以|但是|但|据此)[，,]?(?=\s*(?:本次资料)?(?:未|无法|不能|尚未|没有|缺少))", "", text)
        text = re.sub(r"未标明|未写明|没有说明|未说明|没有明确", "未明确", text)
    return compact(word_forms(text))


def harmless_control(text: str) -> bool:
    """Never exempt a whole 'suggestion/question' if it contains a policy fact."""
    value = text.strip(" 。！？；，,:：\n")
    if not value:
        return True
    # A request for an unspecified attribute is not an assertion about its
    # value. Keep this grammar closed: no digits, operators or policy clauses.
    if re.fullmatch(r"请(?:确认|提供|补充|告知|核实)(?:具体|实际|准确)(?:生效|到账|发放)(?:时间|日期|时点)", value):
        return True
    if re.search(r"\d|[一二三四五六七八九十]+(?:天|元|日)|必须|规定|每年|每月|统一|生效|到账|发放|取决于|通常|一般|可以申请|享有|后才能|仅限|不支持|允许|禁止|公司提供", value):
        return False
    return bool(re.fullmatch(r"(?:建议|请)(?:向|联系|补充|提供|确认|告知|核实|咨询).+", value))


def units(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。！？；\n，,]+", text) if part.strip()]


def source_selection_question(text: str, claims: list[ClaimEvidence], metadata: list[dict]) -> list[dict]:
    """Authenticate labels in a request to choose a source, never choose it.

    Only exact cited titles and literal duration labels already present in a
    DIRECT claim about that citation are allowed. Ordinary numeric questions,
    unmatched titles/values, policy instructions and extra clauses stay checked.
    """
    request=re.fullmatch(r"(?:请问|请确认|请核实)(?:你|您|贵公司|贵部门|本部门|贵单位)?"
        r"(?:应适用(?:的是)?|适用(?:的)?(?:是)?)(.+)",text.strip(' 。！？?\n'))
    if not request:
        return []
    options=re.split(r"还是|或者|或",request[1])
    if not 2<=len(options)<=3:
        return []
    duration=r"\d+(?:\.\d+)?\s*(?:个)?(?:自然日|工作日|小时|个月|分钟|天|日|月|年)"
    rows=[]
    def literal_value(value,source):
        needle=compact(value);haystack=compact(source)
        for found in re.finditer(re.escape(needle),haystack):
            before,after=haystack[:found.start()],haystack[found.end():]
            if re.search(r"\d$|(?:decimalpoint|minusoperator|plusoperator|不|未|无|并非|不是|不为|不得|不能)$",before):
                continue
            if re.match(r"\d|decimalpoint|percentoperator",after):
                continue
            return True
        return False
    for option in options:
        match=re.fullmatch(r"《([^》]+)》(?:[（(]("+duration+r")[）)])?",option.strip())
        if not match:
            return []
        refs=[i for i,item in enumerate(metadata) if compact(item.get('title',''))==compact(match[1])]
        if len(refs)!=1:
            return []
        ref=refs[0];label=match[2]
        support=[i for i,claim in enumerate(claims) if claim.kind=='DIRECT' and ref in claim.citation_indices
            and (not label or literal_value(label,claim.text) and literal_value(label,metadata[ref].get('quote','')))]
        if not support:
            return []
        rows.append(dict(citation_index=ref,doc_id=metadata[ref]['doc_id'],title=match[1],
                         literal_label=label,claim_indices=support))
    if len({row['doc_id'] for row in rows})!=len(rows):
        return []
    return rows


def necessary_relation(text: str, metadata: list[dict] | None = None) -> tuple[str, str] | None:
    """An alternative syntax alignment, not a determination of truth."""
    value = unframe(text, metadata).strip(" 。；\n")
    value = re.sub(r"^(?:据此|基于原文)(?:只能|仅可|可以)?(?:推断|判断|推知)[:：]?", "", value)
    match = re.fullmatch(r"(.+?)是(.+?)的(?:必要条件|必要前提|前置条件)", value)
    if match:
        return compact(word_forms(match.group(1))), compact(word_forms(match.group(2)))
    match = re.fullmatch(r"(.+?)(?:须|必须|需要)以(.+?)为(?:前提|必要条件|必要前提)", value)
    if match:
        return compact(word_forms(match.group(2))), compact(word_forms(match.group(1)))
    return None


@dataclass
class CoverageReview:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    unmapped_fragments: list[dict] = field(default_factory=list)
    affirmative_preface: bool = False
    control_bindings: list[dict] = field(default_factory=list)


def check_coverage(answer: str, claims: list[ClaimEvidence], *, metadata: list[dict] | None = None,
                   segments: list[AnswerSegment] | None = None, parts: dict[str, str] | None = None,
                   question: str | None = None) -> CoverageReview:
    result = CoverageReview()
    claim_ids = [claim.claim_id or f"legacy_{index}" for index, claim in enumerate(claims)]
    if len(set(claim_ids)) != len(claim_ids):
        result.errors.append("duplicate_claim_id")
    by_id = dict(zip(claim_ids, claims))
    combined = answer
    # Only the separately identified terminal question can be exempt as a pure
    # request. Numeric/embedded factual questions remain checked below.
    if parts and parts.get("question"):
        choices=source_selection_question(parts['question'],claims,metadata or [])
        if harmless_control(parts["question"]) or choices:
            combined = "\n".join(value for key, value in parts.items() if key != "question")
            if choices:
                result.control_bindings.append(dict(rule='cited_source_selection_question',question=parts['question'],choices=choices))
    result.affirmative_preface = bool(re.match(r"\s*(?:是的|是)[。！,，\s]+", combined))
    if result.affirmative_preface:
        combined = re.sub(r"^\s*(?:是的|是)[。！,，\s]+", "", combined, count=1)
    normalized_answer = canonical(combined, metadata)
    covered = [False] * len(normalized_answer)
    for index, (claim_id, claim) in enumerate(zip(claim_ids, claims)):
        text = canonical(claim.text, metadata)
        starts = [match.start() for match in re.finditer(re.escape(text), normalized_answer)] if text else []
        relation_matches = []
        signature = necessary_relation(claim.text, metadata)
        if not starts and signature:
            for part in re.split(r"[。！？；\n]+", combined):
                if necessary_relation(part, metadata) == signature:
                    value = canonical(part, metadata)
                    relation_matches += [(match.start(), match.end(), part) for match in re.finditer(re.escape(value), normalized_answer)]
        if not starts and not relation_matches:
            result.errors.append(f"claim_{index}:not_in_answer")
        for start in starts:
            covered[start:start + len(text)] = [True] * len(text)
        for start, end, _ in relation_matches:
            covered[start:end] = [True] * (end - start)
        result.bindings.append({"claim_id": claim_id, "claim_index": index,
                                "claim_text": claim.text,
                                "alignment": "bounded_normalized" if starts else "necessary_relation_form" if relation_matches else "unmapped",
                                "public_relation_texts": [part for _, _, part in relation_matches],
                                "answer_spans_normalized": [[start, start + len(text)] for start in starts]
                                    + [[start, end] for start, end, _ in relation_matches]})
    # Ignore only independently safe control clauses, never arbitrary unclaimed
    # paragraphs, bracketted facts or everything after a question/suggestion.
    for part in units(combined):
        if harmless_control(part):
            value = canonical(part, metadata)
            for match in re.finditer(re.escape(value), normalized_answer) if value else []:
                covered[match.start():match.end()] = [True] * len(value)
    # A question may repeat a cited value as part of its options. Exempt only
    # the request left AFTER every such factual fragment has been mapped. This
    # is not a blanket exemption for numeric or policy-bearing questions.
    if parts and parts.get("question"):
        value = canonical(parts["question"], metadata)
        for match in re.finditer(re.escape(value), normalized_answer) if value else []:
            residual = "".join(normalized_answer[index] for index in range(match.start(), match.end()) if not covered[index])
            if harmless_control(residual):
                covered[match.start():match.end()] = [True] * len(value)
    if any(not item for item in covered):
        result.errors.append("unmapped_answer_text")
        for match in re.finditer(r"0+", "".join("1" if item else "0" for item in covered)):
            result.unmapped_fragments.append({"start_normalized": match.start(), "end_normalized": match.end(),
                                              "text_normalized": normalized_answer[match.start():match.end()][:160],
                                              "draft_units": [part[:240] for part in units(combined)
                                                  if any(m.start()<match.end() and m.end()>match.start()
                                                      for m in re.finditer(re.escape(canonical(part,metadata)),normalized_answer))][:4]})
            if len(result.unmapped_fragments) >= 12:
                break
    if segments:
        if any(claim.claim_id is None for claim in claims):
            result.errors.append("segments_require_stable_claim_ids")
        if len({item.segment_id for item in segments}) != len(segments):
            result.errors.append("duplicate_answer_segment_id")
        actual_parts = parts or {"answer": answer}
        for part_name in ["answer", "reason"]:
            chunks = [item.text for item in segments if item.part == part_name]
            if compact("\n".join(chunks)) != compact(actual_parts.get(part_name, "")):
                result.errors.append(f"segments_do_not_cover_{part_name}")
        for segment in segments:
            if any(ref not in by_id for ref in segment.claim_ids):
                result.errors.append(f"segment_{segment.segment_id}:unknown_claim_id")
                continue
            selected = [by_id[ref] for ref in segment.claim_ids]
            if not selected and not harmless_control(segment.text):
                result.errors.append(f"segment_{segment.segment_id}:unmapped_fact")
            # IDs are associations, not authorization to hide contradictory or
            # additional text. Every non-control fragment must occur in a claim.
            residual = canonical(segment.text, metadata)
            for claim in selected:
                residual = residual.replace(canonical(claim.text, metadata), "")
            if residual and not harmless_control(segment.text):
                result.errors.append(f"segment_{segment.segment_id}:claim_text_mismatch")
            for binding in result.bindings:
                if binding["claim_id"] in segment.claim_ids:
                    binding.setdefault("answer_segment_ids", []).append(segment.segment_id)
    result.errors = list(dict.fromkeys(result.errors))
    return result
