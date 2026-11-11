"""Independent, bounded proposition checks for annotated evidence fixtures.

This is NOT a general natural-language entailment solver. Claim normalization is
AI-assisted review, with a separate pending human-review field in the artifacts.
"""
from __future__ import annotations

from typing import Any


def assess_proposition(claim: dict[str, Any], rule: dict[str, Any]) -> str:
    predicate, value = claim["predicate"], claim["value"]
    if predicate == "eligible":
        allowed = claim["subject"] in rule["allowed_subjects"]
        return "supported" if value == allowed else "contradicted"
    if predicate == "channel_supported":
        allowed = claim["channel"] in rule["allowed_channels"]
        return "supported" if value == allowed else "contradicted"
    if predicate == "requires_approval":
        return "supported" if value == rule["requires_approval"] else "contradicted"
    if predicate == "automatic_effect_on_submission" and rule.get("requires_approval"):
        return "contradicted" if value else "supported"
    if predicate == "immediate_effect_after_submission":
        # A prerequisite alone doesn't specify every possible timing scenario.
        return "not_established"
    if predicate == "approval_effective_time_specified":
        return "supported" if value == rule.get("effective_time_specified", False) else "contradicted"
    return "not_established"
