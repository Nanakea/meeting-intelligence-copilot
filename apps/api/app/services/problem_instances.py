"""Deterministic, provider-neutral problem-instance identity extraction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.domain.analysis import ProposedFact
from app.domain.context import EntityGlossaryEntry, EntityGlossaryKind
from app.domain.contracts import TranscriptEvent

_SYSTEM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SAP|ERP|EC側?|AWS|API|CSV|NetSuite|Salesforce|"
    r"Dynamics(?:\s*365)?|SharePoint|OneDrive|WMS|FBA|Amazon|"
    r"warehouse(?:\s+side)?|倉庫側|창고|아마존)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_OBJECT_RE = re.compile(
    r"(?:inventory|stock|order|invoice|customer|vendor|supplier|project|contract|"
    r"approval|onboarding|pricing|在庫|受注|注文|請求|顧客|仕入先|案件|契約|"
    r"承認|価格|オンボーディング|재고|주문|송장|고객|공급업체|프로젝트|계약|"
    r"승인|가격|온보딩)",
    re.IGNORECASE,
)
_TEXT_ANCHORS: dict[str, tuple[re.Pattern[str], ...]] = {
    "manual_work": (
        re.compile(r"CSV", re.IGNORECASE),
        re.compile(r"(?:order|invoice|inventory)\s+file", re.IGNORECASE),
        re.compile(r"(?:注文|請求|在庫)(?:データ|ファイル|一覧)"),
        re.compile(r"(?:주문|송장|재고)(?:\s*데이터|\s*파일|\s*목록)"),
    ),
    "process_delay": (
        re.compile(
            r"承認プロセス|approval\s+process|onboarding|승인\s*프로세스|온보딩", re.IGNORECASE
        ),
    ),
    "vague_requirement": (
        re.compile(r"検索画面|search\s+(?:screen|page|experience)", re.IGNORECASE),
        re.compile(r"注文ワークフロー|order\s+workflow", re.IGNORECASE),
        re.compile(r"검색\s*화면|주문\s*워크플로", re.IGNORECASE),
    ),
    "unclear_ownership": (
        re.compile(r"値引き承認|discount\s+approval", re.IGNORECASE),
        re.compile(
            r"仕入先承認|vendor\s+approval|pricing\s+exceptions?",
            re.IGNORECASE,
        ),
        re.compile(r"할인\s*승인|공급업체\s*승인|가격\s*예외", re.IGNORECASE),
    ),
    "nonfunctional_requirement": (
        re.compile(r"performance|availability|response\s+time|latency", re.IGNORECASE),
        re.compile(r"性能|可用性|応答時間|レイテンシ|성능|가용성|응답\s*시간|지연\s*시간"),
    ),
    "architecture_constraint": (
        re.compile(r"architecture\s+constraint|mandatory|must\s+use", re.IGNORECASE),
        re.compile(r"アーキテクチャ制約|必須|変更でき|아키텍처\s*제약|필수|변경할\s*수\s*없"),
    ),
    "security_control_gap": (
        re.compile(r"access\s+control|encryption|security\s+control", re.IGNORECASE),
        re.compile(r"アクセス制御|暗号化|セキュリティ統制|접근\s*통제|암호화|보안\s*통제"),
    ),
    "dependency_blocker": (
        re.compile(r"blocked\s+by|waiting\s+for|dependent\s+on", re.IGNORECASE),
        re.compile(r"対応待ち|依存|ブロック|대기|의존|차단"),
    ),
}
_MANUAL_ACTION_RE = re.compile(
    r"(?:manually\s+(?:copy|enter|re-enter|transfer|upload|download)|"
    r"by hand|manual(?:ly)?\s+(?:copy|enter|transfer)|"
    r"手作業|手入力|手動で?(?:コピー|入力|転記|取り込)|"
    r"수작업|수동으로\s*(?:복사|입력|재입력|전송|업로드))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProblemIdentity:
    instance_key: str
    status: str
    subject: str | None


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w.-]+", value, re.UNICODE))[:160]


def _display(value: str) -> str:
    return " ".join(value.split()).strip(" ,、。")[:120]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _glossary_matches(
    text: str,
    glossary: list[EntityGlossaryEntry],
    kind: EntityGlossaryKind,
) -> list[str]:
    """Return canonical names using deterministic longest-alias matching."""

    candidates: list[tuple[int, int, int, str, str]] = []
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    for entry in glossary:
        if not entry.enabled or entry.kind is not kind:
            continue
        for alias in [entry.canonical_name, *entry.aliases]:
            normalized_alias = unicodedata.normalize("NFKC", alias).casefold().strip()
            if not normalized_alias:
                continue
            if re.fullmatch(r"[a-z0-9_. -]+", normalized_alias):
                matched = re.search(
                    rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_])",
                    normalized_text,
                )
            else:
                matched = re.search(re.escape(normalized_alias), normalized_text)
            if matched is not None:
                start, end = matched.span()
                candidates.append(
                    (len(normalized_alias), start, end, normalized_alias, entry.canonical_name)
                )
    candidates.sort(key=lambda item: (-item[0], item[1], item[3], _normalize(item[4])))
    selected: list[tuple[int, int, str]] = []
    for _, start, end, _, canonical_name in candidates:
        if any(
            start < selected_end and end > selected_start
            for selected_start, selected_end, _ in selected
        ):
            continue
        selected.append((start, end, canonical_name))
    selected.sort(key=lambda item: (item[0], item[1], _normalize(item[2])))
    return _unique([_display(item[2]) for item in selected])


def _fact_values(facts: list[ProposedFact], category: str, slot: str) -> list[str]:
    return _unique(
        [
            _display(fact.value)
            for fact in facts
            if fact.template_id == category and fact.slot == slot
        ]
    )


def _anchored_identity(
    category: str,
    labelled_anchors: list[tuple[str, str]],
    subject: str,
) -> ProblemIdentity:
    canonical = "\0".join(
        [category, *(f"{label}:{_normalize(value)}" for label, value in labelled_anchors)]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return ProblemIdentity(f"pi-{digest}", "anchored", subject[:160])


def identify_problem(
    category: str,
    event: TranscriptEvent,
    facts: list[ProposedFact],
    glossary: list[EntityGlossaryEntry] | None = None,
) -> ProblemIdentity:
    """Build an opaque stable key from explicit category-specific anchors."""

    glossary = glossary or []
    systems = _unique(
        [
            *_glossary_matches(event.text, glossary, EntityGlossaryKind.system),
            *_glossary_matches(event.text, glossary, EntityGlossaryKind.interface),
            *[_display(match.group(0)) for match in _SYSTEM_RE.finditer(event.text)],
        ]
    )
    objects = _unique(
        [
            *_glossary_matches(event.text, glossary, EntityGlossaryKind.business_object),
            *[_display(match.group(0)) for match in _OBJECT_RE.finditer(event.text)],
        ]
    )
    glossary_processes = _glossary_matches(event.text, glossary, EntityGlossaryKind.process)

    if category == "data_mismatch":
        objects = _unique([*objects, *_fact_values(facts, category, "affected_scope")])
        if len(systems) >= 2 and objects:
            compared = sorted(systems[:2], key=_normalize)
            labelled = [
                ("system", compared[0]),
                ("system", compared[1]),
                ("object", objects[0]),
            ]
            subject = f"{compared[0]} <-> {compared[1]} {objects[0]} mismatch"
            return _anchored_identity(category, labelled, subject)

    elif category == "integration_failure":
        sources = _fact_values(facts, category, "source_system")
        targets = _fact_values(facts, category, "target_system")
        if sources and targets:
            failure = _fact_values(facts, category, "failure_point")
            labelled = [("source", sources[0]), ("target", targets[0])]
            if objects:
                labelled.append(("object", objects[0]))
            if failure:
                labelled.append(("flow", failure[0]))
            subject_object = f" {objects[0]}" if objects else ""
            subject = f"{sources[0]} -> {targets[0]}{subject_object} integration"
            return _anchored_identity(category, labelled, subject)

    elif category == "manual_work":
        text_anchors = _unique(
            [
                _display(match.group(0))
                for pattern in _TEXT_ANCHORS[category]
                for match in pattern.finditer(event.text)
            ]
        )
        action = _MANUAL_ACTION_RE.search(event.text)
        if action is not None and text_anchors:
            business_object = text_anchors[-1]
            labelled = [("action", "manual-transfer"), ("object", business_object)]
            return _anchored_identity(
                category,
                labelled,
                f"{business_object} manual process",
            )

    elif category in {
        "process_delay",
        "vague_requirement",
        "unclear_ownership",
        "nonfunctional_requirement",
        "architecture_constraint",
        "security_control_gap",
        "dependency_blocker",
    }:
        text_anchors = _unique(
            [
                _display(match.group(0))
                for pattern in _TEXT_ANCHORS.get(category, ())
                for match in pattern.finditer(event.text)
            ]
        )
        process_facts = _unique(
            [
                *_fact_values(facts, category, "process_step"),
                *_fact_values(facts, category, "bottleneck"),
                *_fact_values(facts, category, "decision_process"),
                *_fact_values(facts, category, "quality_attribute"),
                *_fact_values(facts, category, "constraint"),
                *_fact_values(facts, category, "threat_control_gap"),
                *_fact_values(facts, category, "provider"),
            ]
        )
        anchors = _unique([*glossary_processes, *text_anchors, *process_facts])
        normalized_anchors = [_normalize(anchor) for anchor in anchors]
        governed_objects = [
            value
            for value in objects
            if not any(_normalize(value) in anchor for anchor in normalized_anchors)
        ]
        if anchors and governed_objects:
            return _anchored_identity(
                category,
                [("process", anchors[0]), ("object", governed_objects[0])],
                f"{governed_objects[0]} {anchors[0]}",
            )
        if anchors:
            return _anchored_identity(category, [("subject", anchors[0])], anchors[0])
        if len(objects) >= 2:
            return _anchored_identity(
                category,
                [("process", objects[-1]), ("object", objects[0])],
                f"{objects[0]} {objects[-1]}",
            )

    canonical = f"{event.meeting_id}\0{category}\0{event.seq}\0{event.event_id}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return ProblemIdentity(f"pi-{digest}", "provisional", None)
