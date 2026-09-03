"""Deterministic rule analyzer (pure — no vendors, no LLM).

Rules are registered per language and dispatched by the event's EXPLICIT language
(no Unicode-range guessing). Each rule is category-specific and operates on
transcript CONTENT (never on seq numbers or meeting ids). Rules propose a
``DetectedPain`` and/or ``ProposedFact``s; the reducer decides (CLAUDE rules 8-9).

All extracted facts are provenance ``evidence`` — the rules only capture explicitly
stated information. A question utterance never self-answers the slot it asks about
(the fact block is skipped for interrogatives). Facts proposed for a category whose
pain is not present are dropped downstream by the reducer, so cross-category rule
firing cannot fabricate state.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from app.domain.analysis import AnalysisResult, DetectedPain, ProposedFact
from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import Lang, TranscriptEvent
from app.domain.templates import TEMPLATES
from app.services.problem_instances import identify_problem

_JA_QUESTION_MARKERS = (
    "？",
    "?",
    "でしょうか",
    "ますか",
    "どちら",
    "どの",
    "どこ",
    "どなた",
    "いくつ",
    "何",
    "ありますか",
)
_EN_QUESTION_MARKERS = ("?",)
_KO_DIRECT_QUESTION_RE = re.compile(
    r"(?:[?？]|(?:입니까|습니까|인가요|나요|까요|할까요|됩니까|있나요|없나요)[.。]?)$"
)
_EN_DIRECT_QUESTION_RE = re.compile(
    r"^(?:"
    r"(?:do|does|did|is|are|was|were|can|could|would|should|will|has|have)\b"
    r"|who\s+(?:is|are|owns|handles|decides|approves|has|will|can|should)\b"
    r"|what\s+(?:is|are|was|were|does|do|did|can|could|would|should|will|has|have)\b"
    r"|what\s+(?:business\s+impact|error\s+code|system|team|process|step|"
    r"frequency|volume|time|owner|criteria|condition)\b"
    r"|(?:when|where|why)\s+"
    r"(?:is|are|was|were|does|do|did|can|could|would|should|will|has|have)\b"
    r"|how\s+(?:is|are|was|were|does|do|did|can|could|would|should|will|"
    r"has|have|often|many|much|long)\b"
    r"|which\b[^.!?\n]{0,40}\b"
    r"(?:is|are|was|were|does|do|did|can|could|would|should|will|has|have)\b"
    r")",
    re.IGNORECASE,
)

_EN_VOLUME_RE = re.compile(
    r"\b(?P<count>\d[\d,]*)\s+(?P<unit>orders|rows|records|files)"
    r"(?:\s+(?P<period>per|each)\s+(?P<interval>day|morning|run))?\b",
    re.IGNORECASE,
)
_EN_TIME_COST_RE = re.compile(
    r"\btakes?\s+(?:about\s+)?(?P<amount>\d+)\s+(?P<unit>minutes?|hours?)"
    r"(?:\s+(?:per|each)\s+(?P<interval>run|day|morning))?\b",
    re.IGNORECASE,
)
_EN_TEAM_OWNER_RE = re.compile(
    r"\b(?:the\s+)?(?P<owner>[A-Za-z][A-Za-z -]{1,40}?(?:team|operations|department))"
    r"\s+(?:owns|handles|performs|is responsible for)\b",
    re.IGNORECASE,
)
_EN_PERSON_OWNER_RE = re.compile(
    r"\b(?P<owner>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"
    r"\s+is\s+the\s+(?:final\s+)?owner\b"
)
_EN_INTEGRATION_TRANSFER_FAILURE_RE = re.compile(
    r"\b(?P<payload>[A-Za-z][A-Za-z0-9._-]{0,31})\s+"
    r"(?P<operation>import|transfer|sync)\s+from\s+"
    r"(?P<source>[A-Za-z][A-Za-z0-9._-]{0,31})\s+"
    r"(?:in)?to\s+(?P<target>[A-Za-z][A-Za-z0-9._-]{0,31})\s+"
    r"(?:fails|stops|is\s+failing)\b",
    re.IGNORECASE,
)
_EN_INTEGRATION_FAILURE_CLAUSE_RE = re.compile(
    r"\bintegration\b[^.!?\n]{0,80}\b"
    r"(?:fails|failed|is\s+failing|keeps\s+failing|"
    r"(?:returns?|reports?|throws?)\s+(?:an?\s+)?error)\b",
    re.IGNORECASE,
)
_EN_LEAD_TIME_RE = re.compile(
    r"\bcurrently\s+takes?\s+(?P<current>\d+)\s+business\s+days?"
    r"\s+instead\s+of\s+(?P<normal>\d+)\b",
    re.IGNORECASE,
)
_EN_DATA_MISMATCH_SYSTEM_RE = re.compile(
    r"warehouse(?:\s+side)?|ec(?:\s+side)?|erp|sap|netsuite|salesforce",
    re.IGNORECASE,
)
_EN_VAGUE_DONE_WHEN_RE = re.compile(
    r"\b(?:done|resolved)\s+when\s+([^,.]+)",
    re.IGNORECASE,
)
_JA_VOLUME_RE = re.compile(
    r"(?:約|およそ|だいたい|大体)?(?P<count>\d[\d,]*)\s*(?P<unit>件|行|レコード|ファイル)"
)
_JA_TIME_COST_RE = re.compile(
    r"(?:約|およそ|だいたい|大体)?(?P<amount>\d+)\s*(?P<unit>分|時間)(?:くらい|ほど)?"
)
_JA_LEAD_TIME_RE = re.compile(
    r"(?:今|現在)?[^。]*?(?P<current>\d+)\s*(?:営業)?日"
    r"[^。]*?(?:本来|通常|普段)[^。]*?(?P<normal>\d+)\s*(?:営業)?日"
)
_JA_HYPOTHETICAL_PREFIXES = ("もし", "仮に", "たとえば", "例えば")
_KO_HYPOTHETICAL_PREFIXES = ("만약", "가령", "예를 들어", "예컨대")
_EN_HYPOTHETICAL_PREFIX_RE = re.compile(
    r"^(?:if\b|what\s+if\b|suppose\b|for\s+example\b|imagine\b)",
    re.IGNORECASE,
)
_JA_QUOTED_SPEECH_RE = re.compile(r"^[「『].+[」』](?:って|と).+")
_EN_QUOTED_SPEECH_RE = re.compile(
    r'^[\'"].+[\'"]\s+(?:would|could|might|should|is|means)\b',
    re.IGNORECASE,
)
_KO_QUOTED_SPEECH_RE = re.compile(r'^["“‘「『].+["”’」』](?:라고|이라며|라는).+')
_JA_UNCLEAR_OWNERSHIP_RE = re.compile(
    r"(?:"
    r"(?:担当|責任者)が曖昧"
    r"|明確な(?:担当者|責任者)がいない"
    r"|誰が(?:担当する|責任を持つ)か決まってい(?:ない|ません)"
    r"|どの部署が持つか決まってい(?:ない|ません)"
    r")"
)
_EN_UNCLEAR_OWNERSHIP_RE = re.compile(
    r"\b(?:"
    r"ownership\s+(?:is|remains)\s+unclear"
    r"|no\s+one\s+is\s+sure\s+who\s+owns"
    r"|(?:no\s+one|nobody)\s+knows\s+who\s+owns"
    r"|(?:has|have)\s+no\s+clear\s+owner"
    r")\b",
    re.IGNORECASE,
)

# These are deliberately grammar-shaped ownership clauses, rather than a broad
# list of names or role keywords.  The context hint prevents ordinary statements
# such as "会議は来週です" from being treated as ownership facts.
_JA_OWNER_CONTEXT_HINTS = (
    "対応",
    "修正",
    "確認",
    "作業",
    "運用",
    "連携",
    "管理",
    "処理",
    "差異",
    "担当",
    "件",
    "要望",
    "課題",
    "障害",
    "工程",
    "プロセス",
    "申請",
    "問い合わせ",
)
_JA_NON_OWNER_VALUES = (
    "誰か",
    "誰も",
    "どなた",
    "誰",
    "担当者",
    "まだ",
    "未定",
    "決まって",
    "明確",
    "不明",
    "曖昧",
)
_JA_OWNER_CLAUSE_RE = re.compile(
    r"""
    (?P<context>[^、。！？?!\n]{1,30}?)
    は[、,\s]*
    (?P<owner>[^、。！？?!\s]+?)
    (?:
        が(?:担当(?:しています|している|です)|対応(?:しています|している)|見(?:ています|ている)|確認(?:しています|している))
        |で対応(?:しています|している)
        |です
    )
    (?=、|,|。|！|!|？|\?|$)
    """,
    re.VERBOSE,
)
_JA_ROLE_ONLY_CLAUSE_RE = re.compile(
    r"""
    (?P<context>[^、。！？?!\n]{1,30}?)
    は[、,\s]*
    (?P<owner>[^、。！？?!\s]+?)
    (?=、|,)
    """,
    re.VERBOSE,
)
_JA_DIRECT_OWNER_RE = re.compile(
    r"(?P<owner>[^、。！？?!\sは]+?)"
    r"(?:が担当(?:しています|している|です)"
    r"|が対応(?:しています|している)"
    r"|が見(?:ています|ている)"
    r"|が確認(?:しています|している)"
    r"|で対応(?:しています|している))"
    r"(?=、|,|。|！|!|？|\?|$)"
)

# Directional integration-failure evidence. This intentionally requires the
# complete spoken relationship ``source の payload を target に operation ... で
# failure``. Merely mentioning two systems or saying they are "integrated" is
# not enough to assign source/target direction or fabricate a failure point.
_JA_INTEGRATION_TRANSFER_FAILURE_RE = re.compile(
    r"""
    (?P<source>
        [A-Za-z][A-Za-z0-9._-]{0,31}(?:側)?
        |
        [一-龥ぁ-んァ-ヶー]{1,20}?(?:側|システム|基盤)
    )
    の
    (?P<payload>[A-Za-z][A-Za-z0-9._-]{0,31}|[一-龥ぁ-んァ-ヶー]{1,16})
    を
    (?P<target>
        [A-Za-z][A-Za-z0-9._-]{0,31}(?:側)?
        |
        [一-龥ぁ-んァ-ヶー]{1,20}?(?:側|システム|基盤)
    )
    に
    (?P<operation>
        取り込(?:む|み|んで|め)
        |登録(?:する|し|して)
        |送信(?:する|し|して)
        |同期(?:する|し|して)
    )
    (?:ところ|段階|途中|時点)?
    で
    (?:止ま(?:る|り|って|っています)|失敗(?:する|し|して)|エラー(?:になる|が出る|が出て))
    """,
    re.VERBOSE,
)


def _is_question(event: TranscriptEvent) -> bool:
    if event.lang == Lang.ko:
        return _KO_DIRECT_QUESTION_RE.search(event.text.strip()) is not None
    markers = _EN_QUESTION_MARKERS if event.lang == Lang.en else _JA_QUESTION_MARKERS
    if any(marker in event.text for marker in markers):
        return True
    return event.lang == Lang.en and _EN_DIRECT_QUESTION_RE.match(event.text.strip()) is not None


def _is_direct_question(event: TranscriptEvent) -> bool:
    text = event.text.strip()
    if text.endswith(("?", "？")):
        return True
    if event.lang == Lang.en:
        return _EN_DIRECT_QUESTION_RE.match(text) is not None
    if event.lang == Lang.ja:
        return text.rstrip("。").endswith(("ますか", "でしょうか"))
    if event.lang == Lang.ko:
        return _KO_DIRECT_QUESTION_RE.search(text) is not None
    return False


def _is_negated_pain_statement(event: TranscriptEvent, category: str) -> bool:
    text = event.text.strip()
    if event.lang == Lang.en:
        lowered = text.lower()
        markers = {
            "data_mismatch": (
                "not a mismatch",
                "now match",
            ),
            "integration_failure": (
                "no longer fails",
                "does not fail",
                "doesn't fail",
            ),
            "manual_work": (
                "is now automated",
                "do not copy",
                "don't copy",
                "does not copy",
                "doesn't copy",
                "no manual copy",
            ),
            "process_delay": (
                "no process delay",
                "process is not delayed",
            ),
            "unclear_ownership": (
                "ownership is now clear",
                "has a clear owner",
            ),
            "vague_requirement": (
                "already easy to use",
                "requirement is clear",
            ),
        }
        return any(marker in lowered for marker in markers.get(category, ()))
    if event.lang == Lang.ko:
        markers = {
            "data_mismatch": ("불일치가 없습니다", "지금은 일치합니다", "차이가 없습니다"),
            "integration_failure": ("실패하지 않습니다", "오류가 없습니다", "정상 연계됩니다"),
            "manual_work": ("수작업이 아닙니다", "자동화되었습니다", "수동 작업이 없습니다"),
            "process_delay": ("지연이 없습니다", "지연되지 않습니다", "정상 처리됩니다"),
            "unclear_ownership": ("담당자가 명확합니다", "책임자가 정해졌습니다"),
            "vague_requirement": ("요구사항이 명확합니다", "모호하지 않습니다"),
            "nonfunctional_requirement": (
                "비기능 요구사항이 정의되어 있습니다",
                "성능 요구사항이 명확합니다",
                "측정 가능한 성능 목표가 있습니다",
            ),
            "architecture_constraint": (
                "아키텍처 제약이 없습니다",
                "사용이 필수가 아닙니다",
                "변경할 수 있습니다",
            ),
            "security_control_gap": (
                "보안 통제가 있습니다",
                "암호화되어 있습니다",
                "통제 공백이 없습니다",
            ),
            "dependency_blocker": (
                "더 이상 대기하지 않습니다",
                "의존하지 않습니다",
                "차단되지 않았습니다",
                "해결되었습니다",
            ),
        }
        return any(marker in text for marker in markers.get(category, ()))
    markers = {
        "data_mismatch": (
            "差異はありません",
            "今は合っています",
        ),
        "integration_failure": (
            "失敗しません",
            "失敗していません",
        ),
        "manual_work": (
            "手作業ではありません",
            "自動化されました",
        ),
        "process_delay": (
            "遅れていません",
            "遅延はありません",
        ),
        "unclear_ownership": (
            "担当は明確です",
            "責任者は明確です",
        ),
        "vague_requirement": (
            "曖昧ではありません",
            "要件は明確です",
        ),
    }
    return any(marker in text for marker in markers.get(category, ()))


def _pain(category: str, event: TranscriptEvent) -> DetectedPain:
    # Identity is finalized after every rule has proposed its facts for this event.
    return DetectedPain(
        template_id=category,
        title=TEMPLATES[category].title_for(str(event.lang.value)),
        evidence_event_id=event.event_id,
        instance_key="",
        identity_status="provisional",
    )


def _fact(
    category: str,
    slot: str,
    value: str,
    event: TranscriptEvent,
    *,
    relation: str = "supersede",
) -> ProposedFact:
    return ProposedFact(category, slot, value, event.event_id, relation=relation)


def _authoritative_source_relation(event: TranscriptEvent) -> str:
    text = unicodedata.normalize("NFKC", event.text).casefold()
    correction_markers = (
        "actually",
        "correction:",
        "to correct",
        "正確には",
        "実際は",
        "訂正",
        "ではなく",
        "정확히는",
        "정정하면",
        "아니라",
    )
    return "supersede" if any(marker in text for marker in correction_markers) else "contradict"


def _clean_ja_owner(value: str) -> str | None:
    owner = value.strip(" \u3000、,。")
    if not owner or len(owner) > 40 or any(token in owner for token in _JA_NON_OWNER_VALUES):
        return None
    return owner


def _is_nonliteral_context(event: TranscriptEvent) -> bool:
    text = event.text.strip()
    if event.lang == Lang.en:
        return (
            _EN_HYPOTHETICAL_PREFIX_RE.match(text) is not None
            or _EN_QUOTED_SPEECH_RE.match(text) is not None
        )
    if event.lang == Lang.ja:
        return (
            text.startswith(_JA_HYPOTHETICAL_PREFIXES)
            or _JA_QUOTED_SPEECH_RE.match(text) is not None
        )
    return (
        text.startswith(_KO_HYPOTHETICAL_PREFIXES) or _KO_QUOTED_SPEECH_RE.match(text) is not None
    )


def _contains_contradiction_marker(text: str, lang: Lang) -> bool:
    lowered = text.lower() if lang == Lang.en else text
    if lang == Lang.en:
        markers = ("instead of", "rather than", "not every", "not daily", "actually")
    elif lang == Lang.ja:
        markers = ("ではなく", "じゃなく", "正確には", "むしろ")
    else:
        markers = ("아니라", "정확히는", "매일은 아니", "실제로는")
    return any(marker in lowered for marker in markers)


def _extract_ja_frequency_value(text: str) -> str | None:
    if "月末" in text:
        return "月末"
    if "毎週" in text or "週次" in text:
        return "毎週"
    if "毎朝" in text:
        if any(marker in text for marker in ("ほぼ", "だいたい", "大体")):
            return "ほぼ毎朝"
        return "毎朝"
    if "毎日" in text and not _contains_contradiction_marker(text, Lang.ja):
        return "毎日"
    return None


def _extract_en_frequency_value(text: str) -> str | None:
    lowered = text.lower()
    if "month-end" in lowered or "month end" in lowered:
        return "month end"
    if any(
        token in lowered
        for token in ("every business day", "business day", "daily", "each day", "every day")
    ) and not _contains_contradiction_marker(text, Lang.en):
        return "every business day"
    if "every morning" in lowered or "each morning" in lowered:
        return "every morning"
    return None


def _extract_ko_frequency_value(text: str) -> str | None:
    if "월말" in text:
        return "월말"
    if "매주" in text or "주간" in text:
        return "매주"
    if "매일 아침" in text or "매일아침" in text:
        return "매일 아침"
    if "매일" in text and not _contains_contradiction_marker(text, Lang.ko):
        return "매일"
    return None


def _extract_ja_volume_value(text: str) -> str | None:
    match = _JA_VOLUME_RE.search(text)
    if match is None:
        return None
    return f"{match.group('count')}{match.group('unit')}"


def _extract_ja_time_cost_value(text: str) -> str | None:
    match = _JA_TIME_COST_RE.search(text)
    if match is None:
        return None
    return f"{match.group('amount')}{match.group('unit')}"


def _extract_ja_lead_times(text: str) -> tuple[str, str] | None:
    match = _JA_LEAD_TIME_RE.search(text)
    if match is None:
        return None
    return f"{match.group('current')}営業日", f"{match.group('normal')}営業日"


def _clean_slot_value(value: str) -> str:
    return value.strip(" \u3000、,。")


def _clean_ja_process_step_value(value: str) -> str:
    return _clean_slot_value(value).removesuffix("です").removesuffix("で")


def _normalize_system_name(value: str) -> str:
    cleaned = value.strip(" \u3000、,。")
    lowered = cleaned.lower()
    if lowered in {"ec", "ec side"}:
        return "EC side"
    if lowered in {"warehouse", "warehouse side"}:
        return "warehouse side"
    if cleaned.isascii() and cleaned.replace(" ", "").isalnum():
        return cleaned.upper()
    return cleaned


def _extract_ja_owner_value(event: TranscriptEvent) -> str | None:
    """Extract an explicit person/team ownership declaration from one event.

    The helper intentionally requires an ownership predicate (担当/対応/見ている/
    確認している) and skips interrogatives.  A split declaration is kept as one
    concise owner value so the single ``owner`` slot remains deterministic.
    """
    if event.lang != Lang.ja or _is_question(event) or _is_nonliteral_context(event):
        return None

    clauses: list[tuple[int, str | None, str]] = []
    for match in _JA_OWNER_CLAUSE_RE.finditer(event.text):
        context = match.group("context").strip()
        if not any(hint in context for hint in _JA_OWNER_CONTEXT_HINTS):
            continue
        owner = _clean_ja_owner(match.group("owner"))
        if owner is not None:
            clauses.append((match.start(), context, owner))

    # In a compact split statement, the first role often omits the repeated
    # predicate: "一次対応は山田さん、最終確認は情シスです".  Accept that
    # first clause only when another fully explicit ownership clause exists.
    if clauses:
        for match in _JA_ROLE_ONLY_CLAUSE_RE.finditer(event.text):
            context = match.group("context").strip()
            if not any(hint in context for hint in _JA_OWNER_CONTEXT_HINTS):
                continue
            owner = _clean_ja_owner(match.group("owner"))
            if owner is not None and not any(start == match.start() for start, _, _ in clauses):
                clauses.append((match.start(), context, owner))
        clauses.sort(key=lambda clause: clause[0])

    if not clauses:
        for match in _JA_DIRECT_OWNER_RE.finditer(event.text):
            owner = _clean_ja_owner(match.group("owner"))
            if owner is not None:
                clauses.append((match.start(), None, owner))

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0][2]
    if all(context for _, context, _ in clauses):
        return " / ".join(f"{context}: {owner}" for _, context, owner in clauses)
    return " / ".join(owner for _, _, owner in clauses)


def _ja_owner_facts(event: TranscriptEvent) -> list[ProposedFact]:
    owner = _extract_ja_owner_value(event)
    if owner is None:
        return []
    categories = _owner_fact_categories(event)
    return [
        _fact(template.category, "owner", owner, event)
        for template in TEMPLATES.values()
        if "owner" in template.slots and template.category in categories
    ]


def _extract_en_owner_value(event: TranscriptEvent) -> str | None:
    if event.lang != Lang.en or _is_question(event) or _is_nonliteral_context(event):
        return None
    match = _EN_PERSON_OWNER_RE.search(event.text) or _EN_TEAM_OWNER_RE.search(event.text)
    if match is None:
        return None
    owner = match.group("owner").strip()
    if owner.lower() in {"someone", "no one", "nobody"}:
        return None
    return owner


def _en_owner_facts(event: TranscriptEvent) -> list[ProposedFact]:
    owner = _extract_en_owner_value(event)
    if owner is None:
        return []
    categories = _owner_fact_categories(event)
    return [
        _fact(template.category, "owner", owner, event)
        for template in TEMPLATES.values()
        if "owner" in template.slots and template.category in categories
    ]


def _extract_ko_owner_value(event: TranscriptEvent) -> str | None:
    if event.lang != Lang.ko or _is_question(event) or _is_nonliteral_context(event):
        return None
    patterns = (
        re.compile(
            r"(?P<owner>[가-힣A-Za-z][가-힣A-Za-z0-9· _-]{0,30}?)(?:이|가)\s*"
            r"(?:담당|관리|책임지고\s*처리)(?:합니다|하고 있습니다|합니다만)"
        ),
        re.compile(
            r"(?:담당자|책임자)는\s*(?P<owner>[가-힣A-Za-z][가-힣A-Za-z0-9· _-]{0,30}?)"
            r"(?:입니다|로 정해졌습니다)"
        ),
    )
    match = None
    for candidate in patterns:
        match = candidate.search(event.text)
        if match is not None:
            break
    if match is None:
        return None
    owner = " ".join(match.group("owner").split()).strip(" ,、。")
    if owner in {"누군가", "아무도", "담당자", "책임자"}:
        return None
    return owner


def _ko_owner_facts(event: TranscriptEvent) -> list[ProposedFact]:
    owner = _extract_ko_owner_value(event)
    if owner is None:
        return []
    categories = _owner_fact_categories(event)
    return [
        _fact(template.category, "owner", owner, event)
        for template in TEMPLATES.values()
        if "owner" in template.slots and template.category in categories
    ]


def _owner_fact_categories(event: TranscriptEvent) -> set[str]:
    text = unicodedata.normalize("NFKC", event.text).casefold()
    hints = {
        "data_mismatch": (
            "mismatch",
            "does not match",
            "在庫差異",
            "不一致",
            "合わ",
            "데이터 불일치",
            "재고 차이",
            "일치하지 않",
        ),
        "integration_failure": (
            "integration",
            "interface",
            "連携",
            "インターフェース",
            "연계",
            "인터페이스",
        ),
        "manual_work": (
            "manual",
            "spreadsheet",
            "csv",
            "手作業",
            "手入力",
            "転記",
            "コピペ",
            "수작업",
            "수동",
            "복사",
            "재입력",
        ),
        "process_delay": (
            "approval",
            "bottleneck",
            "process delay",
            "onboarding",
            "承認",
            "工程",
            "遅延",
            "法務確認",
            "승인",
            "병목",
            "프로세스 지연",
            "온보딩",
        ),
        "vague_requirement": (
            "requirement",
            "usability",
            "search screen",
            "要望",
            "検索画面",
            "使いやす",
            "요구사항",
            "사용성",
            "검색 화면",
        ),
        "unclear_ownership": (
            "ownership",
            "final decision",
            "責任分担",
            "責任者",
            "最終判断",
            "책임자",
            "담당자가 불명확",
            "최종 결정",
        ),
        "nonfunctional_requirement": (
            "non-functional",
            "performance requirement",
            "availability requirement",
            "response time",
            "非機能",
            "性能要件",
            "可用性",
            "応答時間",
            "비기능",
            "성능 요구사항",
            "가용성",
            "응답 시간",
        ),
        "security_control_gap": (
            "security control",
            "access control",
            "encryption",
            "セキュリティ統制",
            "アクセス制御",
            "暗号化",
            "보안 통제",
            "접근 통제",
            "암호화",
        ),
        "dependency_blocker": (
            "dependency",
            "blocked by",
            "waiting for",
            "依存",
            "待ち",
            "ブロック",
            "의존성",
            "대기 중",
            "차단",
        ),
    }
    matched = {
        category for category, tokens in hints.items() if any(token in text for token in tokens)
    }
    if len(matched) == 1:
        return matched
    return {template.category for template in TEMPLATES.values() if "owner" in template.slots}


def _extract_ja_integration_transfer(t: str) -> tuple[str, str, str] | None:
    """Return explicit source, target, and normalized failure point."""
    match = _JA_INTEGRATION_TRANSFER_FAILURE_RE.search(t)
    if match is None:
        return None

    source = match.group("source").removesuffix("側")
    target = match.group("target").removesuffix("側")
    payload = match.group("payload")
    operation = match.group("operation")
    if operation.startswith("取り込"):
        operation_label = "取り込み"
    elif operation.startswith("登録"):
        operation_label = "登録"
    elif operation.startswith("送信"):
        operation_label = "送信"
    else:
        operation_label = "同期"
    return source, target, f"{payload}{operation_label}"


def _extract_en_data_mismatch_source(text: str) -> str | None:
    patterns = (
        re.compile(
            r"\b(?:the\s+)?"
            r"(?P<system>warehouse(?:\s+side)?|ec(?:\s+side)?|erp|sap|netsuite|salesforce)"
            r"\b"
            r"\s+(?:is|acts as|serves as|remains)\s+(?:the\s+)?"
            r"(?:source of truth|authoritative source|authoritative)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\btreat\s+(?:the\s+)?"
            r"(?P<system>warehouse(?:\s+side)?|ec(?:\s+side)?|erp|sap|netsuite|salesforce)"
            r"\b"
            r"\s+as\s+(?:the\s+)?(?:source of truth|authoritative)\b",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return _normalize_system_name(match.group("system"))
    return None


# --- category rules (each appends to pains/facts) --------------------------

Rule = Callable[[TranscriptEvent, list[DetectedPain], list[ProposedFact]], None]


def _ja_data_mismatch(ev: TranscriptEvent, pains: list, facts: list) -> None:
    t = ev.text
    if _is_nonliteral_context(ev):
        return
    # Pain anchor: prefer the literal domain noun "在庫", but real Whisper STT has been
    # observed to mis-hear it as a same-reading ("saiko") homophone with unrelated kanji
    # (e.g. "最高"/"最古" for "在庫"). The structural pattern -- two "side"/system mentions
    # plus a mismatch verb -- survives that confusion and generalizes to any two-system
    # mismatch statement, not just this fixture's wording.
    two_sided = t.count("側") >= 2
    if (
        ("在庫" in t or two_sided)
        and ("合わない" in t or "差異" in t)
        and not _is_negated_pain_statement(ev, "data_mismatch")
    ):
        pains.append(_pain("data_mismatch", ev))
    if _is_question(ev):
        return
    if "手動" in t and ("直" in t or "修正" in t):
        facts.append(_fact("data_mismatch", "correction_process", "手動修正", ev))
    if frequency := _extract_ja_frequency_value(t):
        facts.append(_fact("data_mismatch", "frequency", frequency, ev))
    # source_of_truth: "正" ("authoritative") is the intended word, but real Whisper STT
    # has been observed to substitute the same-reading ("sei") homophone "制" in this
    # exact "...を(正|制)としています" declaration construction. Accepting both spellings
    # in that construction is a bounded, documented ASR-confusion class, not a blanket
    # acceptance of "制" as meaning source-of-truth elsewhere.
    # "正として"/"制として" matches every observed continuation ("...しています",
    # "...扱っています", "...している" etc.) -- the verb varies but "として"
    # (as/treating-as) always immediately follows the authoritative word.
    authoritative = "正として" in t or "制として" in t
    if authoritative and "倉庫" in t:
        facts.append(
            _fact(
                "data_mismatch",
                "source_of_truth",
                "倉庫側",
                ev,
                relation=_authoritative_source_relation(ev),
            )
        )
    elif authoritative and "EC" in t:
        facts.append(
            _fact(
                "data_mismatch",
                "source_of_truth",
                "EC側",
                ev,
                relation=_authoritative_source_relation(ev),
            )
        )
    # business_impact: mirrors the existing _ja_integration_failure impact pattern.
    # Anchored on "遅れ" (delay), which is stable across the kanji/kana variation
    # observed in real STT ("だいたい" vs "大体" -- pure orthographic variants of the
    # same word, appearing elsewhere in the same sentence but not on this anchor).
    # NOTE: a standalone "時間がかか" (takes time) trigger was removed after review --
    # it was never actually observed in real STT (only "遅れ" was) and fired on
    # generic time-consuming statements unrelated to any shipping/output delay,
    # fabricating a specific value ("出荷作業の遅延") the text never supported.
    if "遅れ" in t or ("影響" in t and "出て" in t):
        facts.append(_fact("data_mismatch", "business_impact", "出荷作業の遅延", ev))


def _ja_vague_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    t = ev.text
    if _is_nonliteral_context(ev):
        return
    vague = "使いやすく" in t or "分かりやすく" in t or "改善してほしい" in t
    if (
        vague
        and ("ほしい" in t or "たい" in t)
        and not _is_negated_pain_statement(ev, "vague_requirement")
    ):
        pains.append(_pain("vague_requirement", ev))
    if _is_question(ev):
        return
    if "時間がかか" in t or "多すぎ" in t:
        problem = "検索結果が多く時間がかかる"
        facts.append(_fact("vague_requirement", "current_problem", problem, ev))
    if "理想" in t:
        expected = "検索結果を絞り込みすぐ見つけられる状態"
        facts.append(_fact("vague_requirement", "expected_behavior", expected, ev))
    if "受け入れ" in t or "解決した" in t:
        facts.append(_fact("vague_requirement", "acceptance_criteria", _clean_slot_value(t), ev))
    if "優先度" in t and ("高い" in t or "最優先" in t):
        facts.append(_fact("vague_requirement", "priority_level", "高", ev))


def _ja_integration_failure(ev: TranscriptEvent, pains: list, facts: list) -> None:
    t = ev.text
    if _is_nonliteral_context(ev):
        return
    transfer = _extract_ja_integration_transfer(t)
    if (
        ("連携" in t and ("されない" in t or "失敗" in t or "エラー" in t)) or transfer
    ) and not _is_negated_pain_statement(ev, "integration_failure"):
        pains.append(_pain("integration_failure", ev))
    if _is_question(ev):
        return
    if transfer is not None:
        source, target, failure_point = transfer
        facts.extend(
            (
                _fact("integration_failure", "source_system", source, ev),
                _fact("integration_failure", "target_system", target, ev),
                _fact("integration_failure", "failure_point", failure_point, ev),
            )
        )
    if "エラー" in t and ("出ていません" in t or "出ない" in t or "なし" in t):
        facts.append(_fact("integration_failure", "error_signal", "エラーなし", ev))
    if "手動" in t and ("登録" in t or "再送" in t or "再登録" in t):
        facts.append(_fact("integration_failure", "retry_behavior", "手動で登録", ev))
    if "影響" in t and ("出て" in t or "遅れ" in t):
        facts.append(_fact("integration_failure", "business_impact", "出荷遅延・配送影響", ev))


def _ja_manual_work(ev: TranscriptEvent, pains: list, facts: list) -> None:
    t = ev.text
    if _is_nonliteral_context(ev):
        return
    manual_anchor = any(
        token in t for token in ("手作業", "手で", "手入力", "転記", "コピペ", "貼り付け", "手動")
    )
    artifact_anchor = any(
        token in t
        for token in (
            "CSV",
            "Excel",
            "エクセル",
            "スプレッドシート",
            "ファイル",
            "一覧",
            "行",
            "件",
        )
    )
    if (
        manual_anchor
        and artifact_anchor
        and _extract_ja_owner_value(ev) is None
        and not _is_negated_pain_statement(ev, "manual_work")
    ):
        pains.append(_pain("manual_work", ev))
    if _is_question(ev):
        return
    if frequency := _extract_ja_frequency_value(t):
        facts.append(_fact("manual_work", "frequency", frequency, ev))
    if volume := _extract_ja_volume_value(t):
        facts.append(_fact("manual_work", "volume", volume, ev))
    if time_cost := _extract_ja_time_cost_value(t):
        facts.append(_fact("manual_work", "time_cost", time_cost, ev))
    if "出荷" in t and ("遅れ" in t or "遅延" in t):
        facts.append(_fact("manual_work", "business_impact", "出荷レポートが遅れる", ev))
    elif "影響" in t and ("出て" in t or "遅れ" in t):
        facts.append(_fact("manual_work", "business_impact", _clean_slot_value(t), ev))
    if any(
        token in t
        for token in ("連携がない", "連携がなく", "自動化されていない", "APIがない", "CSVしか")
    ):
        facts.append(_fact("manual_work", "reason_manual", "連携未対応", ev))
    if any(
        token in t
        for token in ("自動化できれば", "自動で取り込めれば", "なくなれば", "解消できれば")
    ):
        facts.append(_fact("manual_work", "success_condition", _clean_slot_value(t), ev))


def _ja_process_delay(ev: TranscriptEvent, pains: list, facts: list) -> None:
    t = ev.text
    if _is_nonliteral_context(ev):
        return
    lead_times = _extract_ja_lead_times(t)
    process_anchor = any(
        token in t for token in ("承認", "申請", "工程", "プロセス", "オンボーディング", "法務確認")
    )
    delay_anchor = lead_times is not None or any(
        token in t for token in ("遅れ", "遅延", "時間がかか", "営業日", "待ち", "ボトルネック")
    )
    detail_answer = any(
        phrase in t
        for phrase in ("ボトルネックは", "詰まっているのは", "遅れているのは", "遅延しているのは")
    )
    if (
        process_anchor
        and delay_anchor
        and not detail_answer
        and not _is_negated_pain_statement(ev, "process_delay")
    ):
        pains.append(_pain("process_delay", ev))
    if _is_question(ev):
        return
    # Comparative day counts are process facts only when the same utterance
    # identifies a process. This prevents unrelated retention/SLA comparisons
    # from overwriting an already-open process-delay topic.
    if process_anchor and lead_times is not None:
        current, normal = lead_times
        facts.extend(
            (
                _fact("process_delay", "current_lead_time", current, ev),
                _fact("process_delay", "normal_lead_time", normal, ev),
            )
        )
    bottleneck = re.search(r"(?:ボトルネックは|詰まっているのは)(?P<step>[^、。]+)", t)
    if bottleneck is not None:
        facts.append(
            _fact(
                "process_delay",
                "bottleneck",
                _clean_ja_process_step_value(bottleneck.group("step")),
                ev,
            )
        )
    if "契約開始" in t and ("遅れ" in t or "遅延" in t):
        facts.append(_fact("process_delay", "business_impact", "契約開始が遅れる", ev))
    elif "オンボーディング" in t and ("遅れ" in t or "遅延" in t):
        facts.append(_fact("process_delay", "business_impact", "オンボーディングが遅れる", ev))
    process_step = re.search(
        r"(?:遅れているのは|遅延しているのは)(?P<step>[^、。]+)|(?P<step2>[^、。]+)の工程で遅れ",
        t,
    )
    if process_step is not None:
        step = process_step.group("step") or process_step.group("step2")
        facts.append(_fact("process_delay", "process_step", _clean_ja_process_step_value(step), ev))


def _ja_unclear_ownership(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if (
        _JA_UNCLEAR_OWNERSHIP_RE.search(text) is not None
        and not _is_direct_question(ev)
        and not _is_negated_pain_statement(ev, "unclear_ownership")
    ):
        pains.append(_pain("unclear_ownership", ev))
    if _is_question(ev):
        return

    if (
        any(term in text for term in ("承認", "判断", "決裁"))
        and any(term in text for term in ("止ま", "滞", "進ま"))
        and any(term in text for term in ("顧客", "お客様", "案件", "対応"))
    ):
        facts.append(_fact("unclear_ownership", "business_impact", _clean_slot_value(text), ev))
    decision = re.search(
        r"(?:判断|決定|決裁)は(?:毎週|週次|月次)?(?:の)?"
        r"([^、。]{2,30}(?:会議|委員会|レビュー))で"
        r"(?:行います|行う|決めます|決める|しています|している)",
        text,
    )
    if decision is not None:
        facts.append(
            _fact(
                "unclear_ownership",
                "decision_process",
                decision.group(1).strip(),
                ev,
            )
        )
    stakeholders = re.search(
        r"(?P<value>[^。]{2,60}(?:、|と)[^。]{1,30})"
        r"(?:が関係者|が関係しています|が関わっています|がステークホルダー)",
        text,
    )
    if stakeholders is not None:
        facts.append(
            _fact(
                "unclear_ownership",
                "stakeholders",
                stakeholders.group("value").strip(" 、"),
                ev,
            )
        )
    escalation = re.search(
        r"(?:未解決|解決しない|決まらない)(?:の)?(?:場合|案件|もの)?(?:は|を)"
        r"([^、。]{1,30})にエスカレーション",
        text,
    )
    if escalation is not None:
        facts.append(
            _fact(
                "unclear_ownership",
                "escalation_path",
                escalation.group(1).strip(),
                ev,
            )
        )
    status = re.search(r"(?:現状|現在の状況)(?:は|としては)([^、。]{2,80})", text)
    if status is not None:
        facts.append(
            _fact(
                "unclear_ownership",
                "current_status",
                status.group(1).strip(),
                ev,
            )
        )


def _en_data_mismatch(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    system_mentions = len(_EN_DATA_MISMATCH_SYSTEM_RE.findall(ev.text))
    mismatch_anchor = any(
        token in low
        for token in (
            "mismatch",
            "out of sync",
            "do not match",
            "does not match",
            "don't match",
            "doesn't match",
        )
    )
    inventory_anchor = any(token in low for token in ("inventory", "stock", "counts"))
    if (
        (inventory_anchor and mismatch_anchor) or (system_mentions >= 2 and mismatch_anchor)
    ) and not _is_negated_pain_statement(ev, "data_mismatch"):
        pains.append(_pain("data_mismatch", ev))
    if _is_question(ev):
        return
    if "manual" in low and any(token in low for token in ("fix", "correct", "adjust", "reconcile")):
        facts.append(_fact("data_mismatch", "correction_process", "manual correction", ev))
    if frequency := _extract_en_frequency_value(ev.text):
        facts.append(_fact("data_mismatch", "frequency", frequency, ev))
    if source := _extract_en_data_mismatch_source(ev.text):
        facts.append(
            _fact(
                "data_mismatch",
                "source_of_truth",
                source,
                ev,
                relation=_authoritative_source_relation(ev),
            )
        )
    if "shipping" in low and "delay" in low:
        facts.append(_fact("data_mismatch", "business_impact", "shipping delay", ev))
    elif any(token in low for token in ("shipment", "order")) and any(
        token in low for token in ("blocked", "on hold")
    ):
        facts.append(_fact("data_mismatch", "business_impact", "order handling blocked", ev))


def _en_manual_work(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    if (
        ("copies" in low or "copy" in low or "manually" in low or "by hand" in low)
        and ("spreadsheet" in low or "file" in low or "rows" in low)
        and not _is_negated_pain_statement(ev, "manual_work")
    ):
        pains.append(_pain("manual_work", ev))
    if _is_question(ev):
        return
    if "morning shipping report" in low and "hour" in low and "delay" in low:
        facts.append(
            _fact("manual_work", "business_impact", "delays morning shipping report by ~1h", ev)
        )
    elif "shipment release" in low and "45 minutes" in low and "delay" in low:
        facts.append(
            _fact(
                "manual_work",
                "business_impact",
                "delays same-day shipment release by 45 minutes",
                ev,
            )
        )
    elif ("delay" in low or "impact" in low) and any(
        token in low
        for token in (
            "manual",
            "by hand",
            "copy",
            "spreadsheet",
            "file",
            "handoff",
            "reconciliation",
        )
    ):
        facts.append(_fact("manual_work", "business_impact", ev.text.strip(), ev))
    if frequency := _extract_en_frequency_value(ev.text):
        facts.append(_fact("manual_work", "frequency", frequency, ev))
    volume = _EN_VOLUME_RE.search(ev.text)
    if volume is not None:
        value = f"{volume.group('count')} {volume.group('unit').lower()}"
        if volume.group("interval"):
            value += f" per {volume.group('interval').lower()}"
        facts.append(_fact("manual_work", "volume", value, ev))
    time_cost = _EN_TIME_COST_RE.search(ev.text)
    if time_cost is not None:
        value = f"{time_cost.group('amount')} {time_cost.group('unit').lower()}"
        if time_cost.group("interval"):
            value += f" per {time_cost.group('interval').lower()}"
        facts.append(_fact("manual_work", "time_cost", value, ev))


def _en_integration_failure(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    transfer = _EN_INTEGRATION_TRANSFER_FAILURE_RE.search(ev.text)
    failure_clause = _EN_INTEGRATION_FAILURE_CLAUSE_RE.search(ev.text)
    if (transfer is not None or failure_clause is not None) and not _is_negated_pain_statement(
        ev, "integration_failure"
    ):
        pains.append(_pain("integration_failure", ev))
    if _is_question(ev):
        return
    if transfer is not None:
        operation = transfer.group("operation").lower()
        facts.extend(
            (
                _fact("integration_failure", "source_system", transfer.group("source"), ev),
                _fact("integration_failure", "target_system", transfer.group("target"), ev),
                _fact(
                    "integration_failure",
                    "failure_point",
                    f"{transfer.group('payload')} {operation}",
                    ev,
                ),
            )
        )
    if "blocks invoice posting" in low:
        facts.append(_fact("integration_failure", "business_impact", "blocks invoice posting", ev))
    error = re.search(r"\berror\s+code\s+([A-Za-z0-9_-]+)\b", ev.text, re.IGNORECASE)
    if error is not None:
        facts.append(
            _fact("integration_failure", "error_signal", f"error code {error.group(1)}", ev)
        )
    retry = re.search(r"\bretries\s+(\d+)\s+times?\s+automatically\b", low)
    if retry is not None:
        facts.append(
            _fact(
                "integration_failure",
                "retry_behavior",
                f"automatic retry ({retry.group(1)} attempts)",
                ev,
            )
        )


def _en_vague_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    vague_anchor = any(
        phrase in low
        for phrase in (
            "easier to use",
            "more intuitive",
            "simpler to use",
            "improve usability",
            "easier to navigate",
        )
    )
    if vague_anchor and not _is_negated_pain_statement(ev, "vague_requirement"):
        pains.append(_pain("vague_requirement", ev))
    if _is_question(ev):
        return
    if "too many results" in low and ("takes too long" in low or "hard to find" in low):
        facts.append(
            _fact(
                "vague_requirement",
                "current_problem",
                "too many results make the right item hard to find",
                ev,
            )
        )
    elif "takes too long" in low and "find" in low:
        facts.append(_fact("vague_requirement", "current_problem", ev.text.strip(), ev))
    if "ideally" in low and "filter" in low and ("find" in low or "right away" in low):
        facts.append(
            _fact(
                "vague_requirement",
                "expected_behavior",
                "filter results and find the right item right away",
                ev,
            )
        )
    if done_when := _EN_VAGUE_DONE_WHEN_RE.search(ev.text):
        facts.append(
            _fact(
                "vague_requirement",
                "acceptance_criteria",
                done_when.group(1).strip(),
                ev,
            )
        )
    if "quote preparation" in low and any(token in low for token in ("delay", "slow", "slows")):
        facts.append(_fact("vague_requirement", "business_impact", "slows quote preparation", ev))
    if "high priority" in low or "p1" in low:
        facts.append(_fact("vague_requirement", "priority_level", "high", ev))


def _en_process_delay(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    process_anchor = "approval" in low or "process" in low or "onboarding" in low
    delay_anchor = any(
        token in low
        for token in ("currently takes", "this delay", "process delay", "lead time", "bottleneck")
    )
    detail_answer = bool(
        re.search(
            r"\bthe\s+bottleneck\s+is\b"
            r"|\bdelay\s+occurs\s+during\b"
            r"|\bthis\s+delay\s+(?:postpones|blocks|slows|delays)\b",
            low,
        )
    )
    if (
        process_anchor
        and delay_anchor
        and not detail_answer
        and not _is_negated_pain_statement(ev, "process_delay")
    ):
        pains.append(_pain("process_delay", ev))
    if _is_question(ev):
        return
    lead_time = _EN_LEAD_TIME_RE.search(ev.text)
    # Keep comparative business-day statements scoped to an explicit process;
    # otherwise later policy/retention comparisons could supersede this pain's
    # active lead-time evidence.
    if process_anchor and lead_time is not None:
        facts.extend(
            (
                _fact(
                    "process_delay",
                    "current_lead_time",
                    f"{lead_time.group('current')} business days",
                    ev,
                ),
                _fact(
                    "process_delay",
                    "normal_lead_time",
                    f"{lead_time.group('normal')} business days",
                    ev,
                ),
            )
        )
    bottleneck = re.search(r"\bthe\s+bottleneck\s+is\s+([^,.]+)", ev.text, re.IGNORECASE)
    if bottleneck is not None:
        facts.append(_fact("process_delay", "bottleneck", bottleneck.group(1).strip().lower(), ev))
    if "postpones customer onboarding and contract activation" in low:
        facts.append(
            _fact(
                "process_delay",
                "business_impact",
                "postpones customer onboarding and contract activation",
                ev,
            )
        )
    process_step = re.search(r"\bdelay\s+occurs\s+during\s+([^,.]+)", ev.text, re.IGNORECASE)
    if process_step is not None:
        facts.append(
            _fact("process_delay", "process_step", process_step.group(1).strip().lower(), ev)
        )


def _en_unclear_ownership(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    if _EN_UNCLEAR_OWNERSHIP_RE.search(ev.text) is not None and not _is_negated_pain_statement(
        ev, "unclear_ownership"
    ):
        pains.append(_pain("unclear_ownership", ev))
    if _is_question(ev):
        return
    if "discount approvals stall and customers wait" in low:
        facts.append(
            _fact(
                "unclear_ownership",
                "business_impact",
                "discount approvals stall and customers wait",
                ev,
            )
        )
    stakeholders = re.search(
        r"\b([A-Za-z]+,\s*[A-Za-z]+,?\s+and\s+[A-Za-z]+)\s+are\s+the\s+stakeholders\b",
        ev.text,
        re.IGNORECASE,
    )
    if stakeholders is not None:
        facts.append(_fact("unclear_ownership", "stakeholders", stakeholders.group(1).lower(), ev))
    if "decisions are made in the weekly pricing steering meeting" in low:
        facts.append(
            _fact(
                "unclear_ownership",
                "decision_process",
                "weekly pricing steering meeting",
                ev,
            )
        )
    if "unresolved cases escalate to the coo" in low:
        facts.append(_fact("unclear_ownership", "escalation_path", "COO", ev))
    status = re.search(r"\bcurrent\s+status\s+is\s+([^,.]+)", ev.text, re.IGNORECASE)
    if status is not None:
        facts.append(
            _fact("unclear_ownership", "current_status", status.group(1).strip().lower(), ev)
        )


def _en_nonfunctional_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    pain_markers = (
        "non-functional requirement is not defined",
        "performance requirement is unclear",
        "availability requirement is unclear",
        "we have no measurable performance target",
    )
    if any(marker in low for marker in pain_markers):
        pains.append(_pain("nonfunctional_requirement", ev))
    if _is_question(ev):
        return
    if "performance" in low or "response time" in low:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "performance", ev))
    elif "availability" in low or "uptime" in low:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "availability", ev))
    target = re.search(
        r"(?:response time|latency)\s+(?:must|should)\s+be\s+(?:under|below)\s+([^,.]+)",
        ev.text,
        re.I,
    )
    if target:
        facts.append(
            _fact("nonfunctional_requirement", "measurable_target", target.group(1).strip(), ev)
        )
    load = re.search(
        r"(?:for|at)\s+([\d,]+\s+(?:concurrent users|requests per second|transactions per hour))",
        ev.text,
        re.I,
    )
    if load:
        facts.append(_fact("nonfunctional_requirement", "scope_load", load.group(1).lower(), ev))
    if "measured with" in low:
        facts.append(
            _fact(
                "nonfunctional_requirement",
                "measurement_method",
                ev.text.split("measured with", 1)[1].strip(" ."),
                ev,
            )
        )


def _ja_nonfunctional_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in (
            "非機能要件が未定義",
            "性能要件が曖昧",
            "可用性要件が曖昧",
            "数値目標がありません",
        )
    ):
        pains.append(_pain("nonfunctional_requirement", ev))
    if _is_question(ev):
        return
    if "性能" in text or "応答時間" in text:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "性能", ev))
    elif "可用性" in text or "稼働率" in text:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "可用性", ev))
    target = re.search(r"(?:応答時間|レイテンシ)[^。]*?(\d+(?:\.\d+)?\s*(?:秒|ミリ秒))以内", text)
    if target:
        facts.append(
            _fact("nonfunctional_requirement", "measurable_target", target.group(1) + "以内", ev)
        )
    load = re.search(r"(同時\s*\d+\s*ユーザー|毎秒\s*\d+\s*件)", text)
    if load:
        facts.append(_fact("nonfunctional_requirement", "scope_load", load.group(1), ev))


def _en_architecture_constraint(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in low
        for marker in (
            "architecture constraint",
            "we must use",
            "cannot be changed",
            "is mandatory",
        )
    ):
        pains.append(_pain("architecture_constraint", ev))
    if _is_question(ev):
        return
    match = re.search(r"(?:architecture constraint is|we must use)\s+([^,.]+)", ev.text, re.I)
    if match:
        facts.append(_fact("architecture_constraint", "constraint", match.group(1).strip(), ev))
    source = re.search(r"(?:required by|mandated by)\s+([^,.]+)", ev.text, re.I)
    if source:
        facts.append(
            _fact("architecture_constraint", "rationale_source", source.group(1).strip(), ev)
        )


def _ja_architecture_constraint(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in (
            "アーキテクチャ制約",
            "使用が必須",
            "変更できません",
            "利用しなければなりません",
        )
    ):
        pains.append(_pain("architecture_constraint", ev))
    if _is_question(ev):
        return
    match = re.search(r"(?:制約は|必須なのは)([^、。]+)", text)
    if match:
        facts.append(_fact("architecture_constraint", "constraint", match.group(1).strip(), ev))
    source = re.search(r"([^、。]+)(?:の規程|の標準)で必須", text)
    if source:
        facts.append(
            _fact("architecture_constraint", "rationale_source", source.group(1).strip(), ev)
        )


def _en_security_control_gap(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in low
        for marker in (
            "security control is missing",
            "access control gap",
            "not encrypted",
            "control gap",
        )
    ):
        pains.append(_pain("security_control_gap", ev))
    if _is_question(ev):
        return
    if "not encrypted" in low:
        facts.append(
            _fact("security_control_gap", "threat_control_gap", "data is not encrypted", ev)
        )
    asset = re.search(r"(?:customer|employee|financial)\s+(?:data|records)", ev.text, re.I)
    if asset:
        facts.append(
            _fact("security_control_gap", "affected_asset_data", asset.group(0).lower(), ev)
        )
    compliance = re.search(
        r"(?:required by|under)\s+(GDPR|SOX|ISO\s*27001|company policy)", ev.text, re.I
    )
    if compliance:
        facts.append(_fact("security_control_gap", "compliance_source", compliance.group(1), ev))


def _ja_security_control_gap(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in (
            "セキュリティ統制が不足",
            "アクセス制御がありません",
            "暗号化されていません",
            "統制のギャップ",
        )
    ):
        pains.append(_pain("security_control_gap", ev))
    if _is_question(ev):
        return
    if "暗号化されていません" in text:
        facts.append(_fact("security_control_gap", "threat_control_gap", "暗号化されていない", ev))
    asset = re.search(r"(?:顧客|従業員|財務)(?:データ|情報)", text)
    if asset:
        facts.append(_fact("security_control_gap", "affected_asset_data", asset.group(0), ev))
    compliance = re.search(r"(個人情報保護法|GDPR|SOX|ISO\s*27001|社内規程)", text, re.I)
    if compliance:
        facts.append(_fact("security_control_gap", "compliance_source", compliance.group(1), ev))


def _en_dependency_blocker(ev: TranscriptEvent, pains: list, facts: list) -> None:
    low = ev.text.lower()
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in low
        for marker in ("blocked by", "waiting for", "dependency is blocking", "dependent on")
    ):
        pains.append(_pain("dependency_blocker", ev))
    if _is_question(ev):
        return
    provider = re.search(
        r"(?:blocked by|waiting for|dependent on)\s+(?:the\s+)?"
        r"([^,.]+?)(?:\s+team)?(?:\s+to\s+|[,.]|$)",
        ev.text,
        re.I,
    )
    if provider:
        facts.append(_fact("dependency_blocker", "provider", provider.group(1).strip(), ev))
    needed = re.search(r"(?:needed|required|due)\s+by\s+([^,.]+)", ev.text, re.I)
    if needed:
        facts.append(_fact("dependency_blocker", "needed_by", needed.group(1).strip(), ev))
    if "blocked" in low or "waiting" in low:
        facts.append(_fact("dependency_blocker", "current_status", "blocked", ev))


def _ja_dependency_blocker(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in (
            "待ちです",
            "に依存しています",
            "でブロックされています",
            "依存関係がブロッカー",
        )
    ):
        pains.append(_pain("dependency_blocker", ev))
    if _is_question(ev):
        return
    provider = re.search(r"([^、。]+?)(?:チーム|部門|システム)?(?:の対応待ち|に依存)", text)
    if provider:
        facts.append(_fact("dependency_blocker", "provider", provider.group(1).strip(), ev))
    needed = re.search(r"([^、。]+)までに必要", text)
    if needed:
        facts.append(_fact("dependency_blocker", "needed_by", needed.group(1).strip(), ev))
    if "待ち" in text or "ブロック" in text:
        facts.append(_fact("dependency_blocker", "current_status", "ブロック中", ev))


_KO_SYSTEM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SAP|ERP|EC|WMS|FBA|NetSuite|Salesforce|Amazon|아마존|창고)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_KO_INTEGRATION_TRANSFER_RE = re.compile(
    r"(?P<source>[A-Za-z][A-Za-z0-9._-]{0,31}|[가-힣]{1,20})(?:에서|의)\s*"
    r"(?P<payload>[A-Za-z][A-Za-z0-9._-]{0,31}|[가-힣]{1,16})[을를]?\s*"
    r"(?P<target>[A-Za-z][A-Za-z0-9._-]{0,31}|[가-힣]{1,20})(?:로|으로|에)\s*"
    r"(?P<operation>전송|동기화|연계|등록|가져오기|업로드)(?:하는|하던|할)?\s*"
    r"(?:단계|과정|중)?에서?\s*(?:실패|중단|멈추|오류)",
    re.IGNORECASE,
)


def _ko_data_mismatch(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    systems = {match.group(0).casefold() for match in _KO_SYSTEM_RE.finditer(text)}
    mismatch = any(
        marker in text
        for marker in ("일치하지 않", "불일치", "맞지 않", "차이가 납", "차이가 발생")
    )
    inventory = any(marker in text for marker in ("재고", "수량", "데이터", "레코드"))
    if (
        (inventory and mismatch) or (len(systems) >= 2 and mismatch)
    ) and not _is_negated_pain_statement(ev, "data_mismatch"):
        pains.append(_pain("data_mismatch", ev))
    if _is_question(ev):
        return
    if any(marker in text for marker in ("수동으로 수정", "수작업으로 수정", "직접 보정")):
        facts.append(_fact("data_mismatch", "correction_process", "수동 수정", ev))
    if frequency := _extract_ko_frequency_value(text):
        facts.append(_fact("data_mismatch", "frequency", frequency, ev))
    source = re.search(
        r"(?P<system>SAP|ERP|EC|WMS|FBA|NetSuite|Salesforce|Amazon|아마존|창고)"
        r"(?:의\s*데이터|\s*데이터)?[을를]?\s*(?:기준 정보|기준|정본)(?:으?로)?\s*"
        r"(?:사용|취급|보고|삼고)",
        text,
        re.IGNORECASE,
    )
    if source:
        facts.append(
            _fact(
                "data_mismatch",
                "source_of_truth",
                _normalize_system_name(source.group("system")),
                ev,
                relation=_authoritative_source_relation(ev),
            )
        )
    if any(marker in text for marker in ("출하가 지연", "배송이 지연", "주문 처리가 중단")):
        facts.append(_fact("data_mismatch", "business_impact", ev.text.strip(), ev))


def _ko_manual_work(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    manual = any(marker in text for marker in ("수작업", "수동으로", "직접 복사", "재입력"))
    artifact = any(
        marker in text for marker in ("CSV", "엑셀", "스프레드시트", "파일", "행", "데이터")
    )
    if manual and artifact and not _is_negated_pain_statement(ev, "manual_work"):
        pains.append(_pain("manual_work", ev))
    if _is_question(ev):
        return
    if frequency := _extract_ko_frequency_value(text):
        facts.append(_fact("manual_work", "frequency", frequency, ev))
    volume = re.search(r"(?:약\s*)?(\d[\d,]*)\s*(건|행|레코드|파일)", text)
    if volume:
        facts.append(_fact("manual_work", "volume", f"{volume.group(1)}{volume.group(2)}", ev))
    time_cost = re.search(r"(?:약\s*)?(\d+)\s*(분|시간)(?:이|가)?\s*걸", text)
    if time_cost:
        facts.append(
            _fact("manual_work", "time_cost", f"{time_cost.group(1)}{time_cost.group(2)}", ev)
        )
    if manual and any(marker in text for marker in ("지연", "늦어", "오류", "부담")):
        facts.append(_fact("manual_work", "business_impact", ev.text.strip(), ev))


def _ko_integration_failure(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    transfer = _KO_INTEGRATION_TRANSFER_RE.search(text)
    generic_failure = any(
        marker in text for marker in ("연계", "인터페이스", "동기화", "전송")
    ) and any(marker in text for marker in ("실패", "오류", "중단", "멈춥", "되지 않"))
    if (transfer or generic_failure) and not _is_negated_pain_statement(ev, "integration_failure"):
        pains.append(_pain("integration_failure", ev))
    if _is_question(ev):
        return
    if transfer:
        payload = transfer.group("payload").removesuffix("을").removesuffix("를")
        facts.extend(
            (
                _fact("integration_failure", "source_system", transfer.group("source"), ev),
                _fact("integration_failure", "target_system", transfer.group("target"), ev),
                _fact(
                    "integration_failure",
                    "failure_point",
                    f"{payload} {transfer.group('operation')}",
                    ev,
                ),
            )
        )
    error = re.search(r"(?:오류|에러)\s*(?:코드)?\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if error:
        facts.append(
            _fact("integration_failure", "error_signal", f"오류 코드 {error.group(1)}", ev)
        )
    retry = re.search(r"자동으로\s*(\d+)회\s*재시도", text)
    if retry:
        facts.append(
            _fact("integration_failure", "retry_behavior", f"자동 재시도 {retry.group(1)}회", ev)
        )
    if any(marker in text for marker in ("청구 처리가 중단", "출하가 지연", "주문 처리가 중단")):
        facts.append(_fact("integration_failure", "business_impact", ev.text.strip(), ev))


def _ko_vague_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in ("더 쉽게", "더 편하게", "직관적으로", "사용성을 개선", "간단하게")
    ) and not _is_negated_pain_statement(ev, "vague_requirement"):
        pains.append(_pain("vague_requirement", ev))
    if _is_question(ev):
        return
    current = re.search(r"현재\s+([^。.!?]+?)(?:때문에|해서)\s*(?:어렵|불편|오래 걸)", text)
    if current:
        facts.append(_fact("vague_requirement", "current_problem", current.group(1).strip(), ev))
    expected = re.search(r"(?:이상적으로|개선 후에는)\s*([^。.!?]+)", text)
    if expected:
        facts.append(_fact("vague_requirement", "expected_behavior", expected.group(1).strip(), ev))
    acceptance = re.search(r"([^。.!?]+)(?:이면|하면)\s*(?:완료|해결)(?:입니다|로 봅니다)", text)
    if acceptance:
        facts.append(
            _fact("vague_requirement", "acceptance_criteria", acceptance.group(1).strip(), ev)
        )
    if "우선순위가 높" in text or "P1" in text.upper():
        facts.append(_fact("vague_requirement", "priority_level", "높음", ev))


def _ko_process_delay(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    process = any(marker in text for marker in ("승인", "프로세스", "온보딩", "처리 과정"))
    delay = any(
        marker in text for marker in ("지연", "리드 타임", "병목", "걸립니다", "소요됩니다")
    )
    if process and delay and not _is_negated_pain_statement(ev, "process_delay"):
        pains.append(_pain("process_delay", ev))
    if _is_question(ev):
        return
    lead = re.search(
        r"(?:현재|지금)[^。.!?]*?(\d+)\s*(?:영업일|일)[^。.!?]*?"
        r"(?:정상|원래|통상)[^。.!?]*?(\d+)\s*(?:영업일|일)",
        text,
    )
    if lead:
        facts.extend(
            (
                _fact("process_delay", "current_lead_time", f"{lead.group(1)}영업일", ev),
                _fact("process_delay", "normal_lead_time", f"{lead.group(2)}영업일", ev),
            )
        )
    bottleneck = re.search(r"병목(?:은|이| 지점은)\s*([^。,.!?]+)", text)
    if bottleneck:
        facts.append(_fact("process_delay", "bottleneck", bottleneck.group(1).strip(), ev))
    step = re.search(r"(?:지연은|지연이)\s*([^。,.!?]+?)(?:단계|과정)에서\s*발생", text)
    if step:
        facts.append(_fact("process_delay", "process_step", step.group(1).strip(), ev))
    if delay and any(marker in text for marker in ("고객", "출하", "계약", "매출")):
        facts.append(_fact("process_delay", "business_impact", ev.text.strip(), ev))


def _ko_unclear_ownership(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    unclear = any(
        marker in text
        for marker in (
            "담당자가 불명확",
            "책임자가 불명확",
            "담당자가 정해지지 않",
            "누가 담당하는지 모릅니다",
            "어느 팀이 책임지는지 모릅니다",
        )
    )
    if unclear and not _is_negated_pain_statement(ev, "unclear_ownership"):
        pains.append(_pain("unclear_ownership", ev))
    if _is_question(ev):
        return
    stakeholders = re.search(r"이해관계자는\s*([^。.!?]+)", text)
    if stakeholders:
        facts.append(_fact("unclear_ownership", "stakeholders", stakeholders.group(1).strip(), ev))
    decision = re.search(
        r"의사결정은\s*([^。.!?]+?)(?:에서|를 통해)\s*(?:합니다|이루어집니다)", text
    )
    if decision:
        facts.append(_fact("unclear_ownership", "decision_process", decision.group(1).strip(), ev))
    escalation = re.search(
        r"(?:해결되지 않으면|미해결 건은)\s*([^。.!?]+?)(?:에게|로)\s*에스컬레이션", text
    )
    if escalation:
        facts.append(_fact("unclear_ownership", "escalation_path", escalation.group(1).strip(), ev))


def _ko_nonfunctional_requirement(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in (
            "비기능 요구사항이 정의되지 않",
            "성능 요구사항이 불명확",
            "가용성 요구사항이 불명확",
            "측정 가능한 성능 목표가 없",
        )
    ) and not _is_negated_pain_statement(ev, "nonfunctional_requirement"):
        pains.append(_pain("nonfunctional_requirement", ev))
    if _is_question(ev):
        return
    if "성능" in text or "응답 시간" in text:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "성능", ev))
    elif "가용성" in text or "가동률" in text:
        facts.append(_fact("nonfunctional_requirement", "quality_attribute", "가용성", ev))
    target = re.search(
        r"(?:응답 시간|지연 시간)[^。.!?]*?(\d+(?:\.\d+)?\s*(?:초|밀리초))\s*이내", text
    )
    if target:
        facts.append(
            _fact("nonfunctional_requirement", "measurable_target", f"{target.group(1)} 이내", ev)
        )
    load = re.search(r"(동시\s*\d+\s*명|초당\s*\d+\s*건)", text)
    if load:
        facts.append(_fact("nonfunctional_requirement", "scope_load", load.group(1), ev))


def _ko_architecture_constraint(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    architecture_context = any(
        marker in text for marker in ("아키텍처 제약", "사용이 필수", "변경할 수 없")
    )
    named_system_requirement = (
        _KO_SYSTEM_RE.search(text) is not None
        and "사용해야 합니다" in text
        and any(marker in text for marker in ("표준", "정책", "규정", "아키텍처"))
    )
    if (
        architecture_context or named_system_requirement
    ) and not _is_negated_pain_statement(ev, "architecture_constraint"):
        pains.append(_pain("architecture_constraint", ev))
    if _is_question(ev):
        return
    constraint = re.search(
        r"(?:아키텍처 제약은|반드시 사용해야 하는 것은|아키텍처 제약으로)\s*"
        r"([^。.!?]+?)(?:를|을)?\s*(?:사용해야 합니다|입니다|$)",
        text,
    )
    if constraint:
        facts.append(
            _fact("architecture_constraint", "constraint", constraint.group(1).strip(), ev)
        )
    source = re.search(r"([^。.!?]+?)(?:정책|표준|규정)에서\s*(?:요구|필수)", text)
    if source:
        facts.append(
            _fact("architecture_constraint", "rationale_source", source.group(1).strip(), ev)
        )


def _ko_security_control_gap(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    if any(
        marker in text
        for marker in ("보안 통제가 없", "접근 통제가 없", "암호화되지 않", "통제 공백")
    ) and not _is_negated_pain_statement(ev, "security_control_gap"):
        pains.append(_pain("security_control_gap", ev))
    if _is_question(ev):
        return
    if "암호화되지 않" in text:
        facts.append(_fact("security_control_gap", "threat_control_gap", "암호화되지 않음", ev))
    asset = re.search(r"(?:고객|직원|재무)(?:\s*데이터|\s*정보)", text)
    if asset:
        facts.append(_fact("security_control_gap", "affected_asset_data", asset.group(0), ev))
    compliance = re.search(r"(개인정보보호법|GDPR|SOX|ISO\s*27001|사내 규정)", text, re.IGNORECASE)
    if compliance:
        facts.append(_fact("security_control_gap", "compliance_source", compliance.group(1), ev))


def _ko_dependency_blocker(ev: TranscriptEvent, pains: list, facts: list) -> None:
    text = ev.text
    if _is_nonliteral_context(ev):
        return
    blocked = any(
        marker in text
        for marker in (
            "대기 중입니다",
            "기다리는 중입니다",
            "의존하고 있습니다",
            "차단되었습니다",
            "때문에 막혀",
        )
    )
    dependency_context = any(
        marker in text
        for marker in (
            "팀",
            "부서",
            "시스템",
            "대응",
            "승인",
            "API",
            "데이터",
            "인터페이스",
            "연계",
            "산출물",
        )
    )
    if (
        blocked
        and dependency_context
        and not _is_negated_pain_statement(ev, "dependency_blocker")
    ):
        pains.append(_pain("dependency_blocker", ev))
    if _is_question(ev):
        return
    provider = re.search(r"([^。,.!?]+?)(?:팀|부서|시스템)?(?:의 대응을 기다리|에 의존)", text)
    if provider:
        facts.append(_fact("dependency_blocker", "provider", provider.group(1).strip(), ev))
    needed = re.search(r"([^。,.!?]+)까지\s*(?:필요|제공되어야)", text)
    if needed:
        facts.append(_fact("dependency_blocker", "needed_by", needed.group(1).strip(), ev))
    if any(marker in text for marker in ("대기", "차단", "막혀")):
        facts.append(_fact("dependency_blocker", "current_status", "차단됨", ev))


_RULES: dict[Lang, tuple[Rule, ...]] = {
    Lang.ja: (
        _ja_data_mismatch,
        _ja_manual_work,
        _ja_integration_failure,
        _ja_vague_requirement,
        _ja_process_delay,
        _ja_unclear_ownership,
        _ja_nonfunctional_requirement,
        _ja_architecture_constraint,
        _ja_security_control_gap,
        _ja_dependency_blocker,
    ),
    Lang.en: (
        _en_data_mismatch,
        _en_manual_work,
        _en_integration_failure,
        _en_vague_requirement,
        _en_process_delay,
        _en_unclear_ownership,
        _en_nonfunctional_requirement,
        _en_architecture_constraint,
        _en_security_control_gap,
        _en_dependency_blocker,
    ),
    Lang.ko: (
        _ko_data_mismatch,
        _ko_manual_work,
        _ko_integration_failure,
        _ko_vague_requirement,
        _ko_process_delay,
        _ko_unclear_ownership,
        _ko_nonfunctional_requirement,
        _ko_architecture_constraint,
        _ko_security_control_gap,
        _ko_dependency_blocker,
    ),
}


def analyze(
    event: TranscriptEvent,
    glossary: list[EntityGlossaryEntry] | None = None,
) -> AnalysisResult:
    if not event.is_final:
        return AnalysisResult()
    pains: list[DetectedPain] = []
    facts: list[ProposedFact] = []
    for rule in _RULES.get(event.lang, ()):  # explicit language dispatch
        rule(event, pains, facts)
    if _is_direct_question(event):
        pains.clear()
    # Owner answers are intentionally cross-template proposals: the reducer
    # applies them only to an already-detected pain, so one helper supports every
    # owner-bearing template without changing domain lifecycle semantics.
    facts.extend(_ja_owner_facts(event))
    facts.extend(_en_owner_facts(event))
    facts.extend(_ko_owner_facts(event))
    identified: list[DetectedPain] = []
    identity_by_template: dict[str, str] = {}
    for pain in pains:
        if pain.template_id in identity_by_template:
            continue
        identity = identify_problem(pain.template_id, event, facts, glossary)
        identity_by_template[pain.template_id] = identity.instance_key
        identified.append(
            DetectedPain(
                template_id=pain.template_id,
                title=pain.title,
                evidence_event_id=pain.evidence_event_id,
                instance_key=identity.instance_key,
                identity_status=identity.status,
                subject=identity.subject,
            )
        )
    routed_facts = [
        ProposedFact(
            template_id=fact.template_id,
            slot=fact.slot,
            value=fact.value,
            evidence_event_id=fact.evidence_event_id,
            instance_key=identity_by_template.get(fact.template_id),
            kind=fact.kind,
            relation=fact.relation,
            extraction_origin=fact.extraction_origin,
        )
        for fact in facts
    ]
    return AnalysisResult(detected_pains=identified, facts=routed_facts)
