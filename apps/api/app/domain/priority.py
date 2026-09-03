"""Auditable template-first priority strategies."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import FactStatus, InformationGap, MeetingFact, PainPoint


@dataclass(frozen=True)
class RankedGap:
    gap: InformationGap
    effective_priority: int
    signal: str | None = None


_SIGNALS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    (
        "security_or_regulatory",
        (
            "security",
            "regulatory",
            "compliance",
            "breach",
            "セキュリティ",
            "規制",
            "法令",
            "漏えい",
        ),
        -3,
    ),
    (
        "production_interruption",
        ("production", "outage", "service down", "本番", "停止", "障害"),
        -3,
    ),
    ("customer_impact", ("customer", "shipment", "billing", "顧客", "出荷", "請求"), -2),
    (
        "financial_close",
        ("financial close", "month-end close", "accounting close", "決算", "月次締め", "会計締め"),
        -2,
    ),
    ("explicit_deadline", ("deadline", "due by", "needed by", "期限", "までに", "締切"), -1),
)


class SolutionLeadPriorityStrategy:
    """Preserve template order, then apply bounded explicit impact signals."""

    def rank(
        self,
        gaps: list[InformationGap],
        pains: list[PainPoint],
        facts: list[MeetingFact],
    ) -> list[RankedGap]:
        created_order = {pain.pain_id: pain.created_seq for pain in pains}
        active_text: dict[str, str] = {}
        for fact in facts:
            if fact.status is FactStatus.active:
                active_text[fact.pain_id] = (
                    f"{active_text.get(fact.pain_id, '')} {fact.value}".casefold()
                )
        ranked: list[RankedGap] = []
        for gap in gaps:
            adjustment = 0
            matched_signal: str | None = None
            text = active_text.get(gap.pain_id, "")
            for signal, markers, value in _SIGNALS:
                if any(marker.casefold() in text for marker in markers) and value < adjustment:
                    adjustment = value
                    matched_signal = signal
            ranked.append(RankedGap(gap, max(0, gap.base_priority + adjustment), matched_signal))
        ranked.sort(
            key=lambda value: (
                value.effective_priority,
                created_order.get(value.gap.pain_id, 0),
                value.gap.base_priority,
                value.gap.gap_id,
            )
        )
        return ranked


solution_lead_priority = SolutionLeadPriorityStrategy()
