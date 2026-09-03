"""Pain templates as data (Slice 2).

A template declares an ordered list of slots — the order IS the base priority
(DOMAIN_MODEL §2). There is deliberately NO universal cross-template ordering
(CLAUDE rule 9). The reducer and selector use this registry generically; no
category-specific branching lives in orchestration. Question wording (per
language) lives here, not in the selector algorithm."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotQuestion:
    text: str
    reason: str


@dataclass(frozen=True)
class PainTemplate:
    category: str
    # localized pain-point titles: language -> title
    title: dict[str, str]
    # ordered slots; index = base priority (lower index = higher priority)
    slots: tuple[str, ...]
    # localized questions: language -> slot -> SlotQuestion
    questions: dict[str, dict[str, SlotQuestion]]

    def priority_of(self, slot: str) -> int:
        return self.slots.index(slot)

    def question(self, lang: str, slot: str) -> SlotQuestion | None:
        return self.questions.get(lang, {}).get(slot)

    def title_for(self, lang: str) -> str:
        return self.title.get(lang) or next(iter(self.title.values()))


_KO_SLOT_QUESTIONS: dict[str, SlotQuestion] = {
    "source_of_truth": SlotQuestion(
        "현재 어떤 시스템의 데이터를 기준 정보로 사용하고 있습니까?",
        "기준 정보 시스템이 확인되지 않았습니다.",
    ),
    "business_impact": SlotQuestion(
        "이 문제가 업무에 어떤 영향을 주고 있습니까?",
        "업무 영향이 확인되지 않았습니다.",
    ),
    "owner": SlotQuestion("이 문제의 담당자는 누구입니까?", "담당자가 확인되지 않았습니다."),
    "affected_scope": SlotQuestion(
        "어떤 제품, 조직 또는 범위가 영향을 받고 있습니까?",
        "영향 범위가 확인되지 않았습니다.",
    ),
    "frequency": SlotQuestion(
        "이 문제는 얼마나 자주 발생합니까?", "발생 빈도가 확인되지 않았습니다."
    ),
    "correction_process": SlotQuestion(
        "현재 이 문제를 어떻게 수정하고 있습니까?",
        "현재 수정 절차가 확인되지 않았습니다.",
    ),
    "volume": SlotQuestion(
        "한 번에 처리하는 데이터 양은 얼마입니까?", "처리량이 확인되지 않았습니다."
    ),
    "time_cost": SlotQuestion(
        "이 작업에는 매번 얼마나 걸립니까?", "소요 시간이 확인되지 않았습니다."
    ),
    "reason_manual": SlotQuestion(
        "이 단계가 수작업인 이유는 무엇입니까?", "수작업인 이유가 확인되지 않았습니다."
    ),
    "success_condition": SlotQuestion(
        "어떤 상태가 되면 이 문제가 해결되었다고 판단합니까?",
        "완료 조건이 정의되지 않았습니다.",
    ),
    "failure_point": SlotQuestion(
        "연계 흐름의 어느 단계에서 실패합니까?", "실패 지점이 확인되지 않았습니다."
    ),
    "retry_behavior": SlotQuestion(
        "실패 시 재시도 또는 재전송이 수행됩니까?", "재시도 동작이 확인되지 않았습니다."
    ),
    "error_signal": SlotQuestion(
        "실패 시 오류나 알림이 발생합니까?", "오류 신호가 확인되지 않았습니다."
    ),
    "source_system": SlotQuestion(
        "데이터를 보내는 원본 시스템은 무엇입니까?", "원본 시스템이 확인되지 않았습니다."
    ),
    "target_system": SlotQuestion(
        "데이터를 받는 대상 시스템은 무엇입니까?", "대상 시스템이 확인되지 않았습니다."
    ),
    "expected_behavior": SlotQuestion(
        "개선 후의 이상적인 동작은 무엇입니까?", "기대 동작이 구체화되지 않았습니다."
    ),
    "acceptance_criteria": SlotQuestion(
        "어떤 기준으로 완료를 판단합니까?", "인수 기준이 정의되지 않았습니다."
    ),
    "current_problem": SlotQuestion(
        "현재 구체적으로 어떤 점이 문제입니까?", "현재 문제가 구체화되지 않았습니다."
    ),
    "priority_level": SlotQuestion(
        "이 요청의 우선순위는 어느 정도입니까?", "우선순위가 확인되지 않았습니다."
    ),
    "bottleneck": SlotQuestion("어느 단계가 병목입니까?", "병목 지점이 확인되지 않았습니다."),
    "current_lead_time": SlotQuestion(
        "현재 전체 처리 시간은 얼마입니까?", "현재 리드 타임이 확인되지 않았습니다."
    ),
    "normal_lead_time": SlotQuestion(
        "정상적으로는 얼마나 걸려야 합니까?", "정상 리드 타임이 확인되지 않았습니다."
    ),
    "process_step": SlotQuestion(
        "지연은 어느 단계에서 발생합니까?", "지연 단계가 확인되지 않았습니다."
    ),
    "decision_process": SlotQuestion(
        "이 사안은 어떤 절차로 결정합니까?", "의사결정 절차가 확인되지 않았습니다."
    ),
    "stakeholders": SlotQuestion(
        "관련 이해관계자는 누구입니까?", "이해관계자가 확인되지 않았습니다."
    ),
    "escalation_path": SlotQuestion(
        "해결되지 않으면 어디로 에스컬레이션합니까?", "에스컬레이션 경로가 확인되지 않았습니다."
    ),
    "current_status": SlotQuestion("현재 상태는 어떻습니까?", "현재 상태가 확인되지 않았습니다."),
    "quality_attribute": SlotQuestion(
        "어떤 비기능 품질 특성이 중요합니까?", "품질 특성이 확인되지 않았습니다."
    ),
    "measurable_target": SlotQuestion(
        "측정 가능한 목표값은 무엇입니까?", "측정 가능한 목표가 정의되지 않았습니다."
    ),
    "scope_load": SlotQuestion(
        "어떤 범위와 부하 조건에 적용됩니까?", "범위와 부하가 확인되지 않았습니다."
    ),
    "measurement_method": SlotQuestion(
        "목표를 어떻게 측정하고 검증합니까?", "측정 방법이 확인되지 않았습니다."
    ),
    "constraint": SlotQuestion(
        "반드시 지켜야 하는 제약은 무엇입니까?", "아키텍처 제약이 확인되지 않았습니다."
    ),
    "rationale_source": SlotQuestion(
        "이 제약의 근거나 출처는 무엇입니까?", "제약의 근거가 확인되지 않았습니다."
    ),
    "affected_systems": SlotQuestion(
        "이 제약의 영향을 받는 시스템은 무엇입니까?", "영향 시스템이 확인되지 않았습니다."
    ),
    "decision_authority": SlotQuestion(
        "이 제약의 예외를 승인할 권한은 누구에게 있습니까?", "의사결정 권한이 확인되지 않았습니다."
    ),
    "alternatives": SlotQuestion("검토한 대안은 무엇입니까?", "대안이 확인되지 않았습니다."),
    "exception_path": SlotQuestion(
        "예외가 필요한 경우 어떤 절차를 따릅니까?", "예외 절차가 확인되지 않았습니다."
    ),
    "affected_asset_data": SlotQuestion(
        "어떤 자산이나 데이터가 영향을 받습니까?",
        "영향받는 자산 또는 데이터가 확인되지 않았습니다.",
    ),
    "threat_control_gap": SlotQuestion(
        "구체적인 위협 또는 통제 공백은 무엇입니까?", "통제 공백이 확인되지 않았습니다."
    ),
    "compliance_source": SlotQuestion(
        "적용되는 규정이나 정책은 무엇입니까?", "규정 또는 정책 근거가 확인되지 않았습니다."
    ),
    "target_date": SlotQuestion(
        "언제까지 통제를 적용해야 합니까?", "목표 날짜가 확인되지 않았습니다."
    ),
    "provider": SlotQuestion(
        "의존 항목을 제공해야 하는 팀이나 시스템은 무엇입니까?", "제공자가 확인되지 않았습니다."
    ),
    "consumer": SlotQuestion(
        "이 의존 항목을 필요로 하는 팀이나 시스템은 무엇입니까?", "소비자가 확인되지 않았습니다."
    ),
    "deliverable_interface": SlotQuestion(
        "필요한 산출물 또는 인터페이스는 무엇입니까?",
        "산출물 또는 인터페이스가 확인되지 않았습니다.",
    ),
    "needed_by": SlotQuestion("언제까지 필요합니까?", "필요 기한이 확인되지 않았습니다."),
    "fallback": SlotQuestion(
        "기한을 맞추지 못할 경우 대안은 무엇입니까?", "대안이 확인되지 않았습니다."
    ),
}


def _q(ja_text: str, ja_reason: str, en_text: str, en_reason: str) -> dict[str, SlotQuestion]:
    """Build the per-language SlotQuestion pair for one slot."""
    return {
        "ja": SlotQuestion(ja_text, ja_reason),
        "en": SlotQuestion(en_text, en_reason),
    }


def _by_lang(slot_map: dict[str, dict[str, SlotQuestion]]) -> dict[str, dict[str, SlotQuestion]]:
    """Transpose {slot: {lang: SlotQuestion}} -> {lang: {slot: SlotQuestion}}."""
    out: dict[str, dict[str, SlotQuestion]] = {"ja": {}, "en": {}, "ko": {}}
    for slot, per_lang in slot_map.items():
        for lang, sq in per_lang.items():
            out[lang][slot] = sq
        out["ko"][slot] = _KO_SLOT_QUESTIONS[slot]
    return out


DATA_MISMATCH = PainTemplate(
    category="data_mismatch",
    title={
        "ja": "EC・倉庫間の在庫差異",
        "en": "Data mismatch between systems",
        "ko": "시스템 간 데이터 불일치",
    },
    slots=(
        "source_of_truth",
        "business_impact",
        "owner",
        "affected_scope",
        "frequency",
        "correction_process",
    ),
    questions=_by_lang(
        {
            "source_of_truth": _q(
                "現在、在庫の正として扱っているのはEC側と倉庫側のどちらでしょうか？",
                "在庫の正となるデータソースが未確定です。",
                "Which system is currently treated as the source of truth for inventory?",
                "The authoritative data source is still unclear.",
            ),
            "business_impact": _q(
                "この差異により、業務にどのような影響が出ていますか？",
                "差異による業務影響が把握できていません。",
                "What business impact is this mismatch causing?",
                "The business impact is not yet understood.",
            ),
            "owner": _q(
                "この差異の対応は、どなたが担当されていますか？",
                "対応の責任者が不明です。",
                "Who owns resolving this mismatch?",
                "The owner is unclear.",
            ),
            "affected_scope": _q(
                "この差異は、どの商品や倉庫の範囲で発生していますか？",
                "影響範囲が特定できていません。",
                "Which products or warehouses are affected?",
                "The affected scope is not identified.",
            ),
            "frequency": _q(
                "この差異は、どのくらいの頻度で発生していますか？",
                "発生頻度が不明です。",
                "How often does this mismatch occur?",
                "The frequency is unknown.",
            ),
            "correction_process": _q(
                "現在、この差異はどのように修正していますか？",
                "修正の手順が不明です。",
                "How is the mismatch currently corrected?",
                "The correction process is unknown.",
            ),
        }
    ),
)

MANUAL_WORK = PainTemplate(
    category="manual_work",
    title={"ja": "手作業による運用", "en": "Manual workaround", "ko": "수작업 운영"},
    slots=(
        "business_impact",
        "frequency",
        "volume",
        "time_cost",
        "owner",
        "reason_manual",
        "success_condition",
    ),
    questions=_by_lang(
        {
            "business_impact": _q(
                "この手作業が業務に与えている影響は何ですか？",
                "手作業による業務影響が不明です。",
                "What business impact does this manual work have?",
                "The business impact is not yet understood.",
            ),
            "frequency": _q(
                "この作業はどのくらいの頻度で必要ですか？",
                "作業の発生頻度が不明です。",
                "How often is this process required?",
                "The frequency is unknown.",
            ),
            "volume": _q(
                "一回あたり、どのくらいの量を処理していますか？",
                "処理量が不明です。",
                "How much data is processed each time?",
                "The volume is unknown.",
            ),
            "time_cost": _q(
                "この作業には毎回どのくらいの時間がかかっていますか？",
                "所要時間が不明です。",
                "How much time does this take each time?",
                "The time cost is unknown.",
            ),
            "owner": _q(
                "この作業は、どなたが担当されていますか？",
                "担当者が不明です。",
                "Who performs this work?",
                "The owner is unclear.",
            ),
            "reason_manual": _q(
                "この作業が手作業になっている理由は何ですか？",
                "手作業になっている理由が不明です。",
                "Why is this step done manually?",
                "The reason it is manual is unclear.",
            ),
            "success_condition": _q(
                "この作業が自動化・解消されたと言える条件は何ですか？",
                "解消条件が定義されていません。",
                "What would 'solved' look like for this work?",
                "The success condition is undefined.",
            ),
        }
    ),
)

INTEGRATION_FAILURE = PainTemplate(
    category="integration_failure",
    title={
        "ja": "システム間連携の不具合",
        "en": "System integration failure",
        "ko": "시스템 연계 오류",
    },
    slots=(
        "business_impact",
        "failure_point",
        "owner",
        "retry_behavior",
        "error_signal",
        "source_system",
        "target_system",
    ),
    questions=_by_lang(
        {
            "business_impact": _q(
                "この連携不具合による業務への影響は何ですか？",
                "連携不具合による業務影響が不明です。",
                "What business impact does this integration failure cause?",
                "The business impact is not yet understood.",
            ),
            "failure_point": _q(
                "どの処理・工程で連携が失敗していますか？",
                "失敗している箇所が特定できていません。",
                "At what point in the flow does the integration fail?",
                "The failure point is not identified.",
            ),
            "owner": _q(
                "この連携の対応は、どなたが担当されていますか？",
                "対応の責任者が不明です。",
                "Who owns this integration issue?",
                "The owner is unclear.",
            ),
            "retry_behavior": _q(
                "連携が失敗した場合、再送や再試行は行われていますか？",
                "リトライの挙動が不明です。",
                "Is there any retry or re-send when it fails?",
                "The retry behavior is unknown.",
            ),
            "error_signal": _q(
                "失敗した際に、エラーやアラートは出ていますか？",
                "エラーの有無が不明です。",
                "Is any error or alert raised on failure?",
                "The error signal is unknown.",
            ),
            "source_system": _q(
                "連携元のシステムはどれですか？",
                "連携元システムが特定できていません。",
                "Which system is the source of the data?",
                "The source system is not identified.",
            ),
            "target_system": _q(
                "連携先のシステムはどれですか？",
                "連携先システムが特定できていません。",
                "Which system is the target of the data?",
                "The target system is not identified.",
            ),
        }
    ),
)

VAGUE_REQUIREMENT = PainTemplate(
    category="vague_requirement",
    title={"ja": "要求内容が曖昧", "en": "Vague requirement", "ko": "모호한 요구사항"},
    slots=(
        "expected_behavior",
        "acceptance_criteria",
        "business_impact",
        "current_problem",
        "owner",
        "priority_level",
    ),
    questions=_by_lang(
        {
            "expected_behavior": _q(
                "改善後は、どのような状態になっていれば理想的でしょうか？",
                "望ましい状態が具体化されていません。",
                "What should the ideal end state look like?",
                "The desired behavior is not yet specified.",
            ),
            "acceptance_criteria": _q(
                "どうなれば「解決した」と判断できますか？",
                "受け入れ基準が定義されていません。",
                "How will we know this is 'done'?",
                "The acceptance criteria are undefined.",
            ),
            "business_impact": _q(
                "この課題は業務にどのような影響を与えていますか？",
                "業務影響が把握できていません。",
                "What business impact does this issue have?",
                "The business impact is not yet understood.",
            ),
            "current_problem": _q(
                "今、具体的にどの部分で困っていますか？",
                "現状の具体的な課題が不明です。",
                "What specifically is difficult today?",
                "The concrete current problem is unclear.",
            ),
            "owner": _q(
                "この要望は、どなたが主管されていますか？",
                "主管者が不明です。",
                "Who owns this request?",
                "The owner is unclear.",
            ),
            "priority_level": _q(
                "この要望の優先度はどの程度でしょうか？",
                "優先度が不明です。",
                "How high a priority is this request?",
                "The priority is unknown.",
            ),
        }
    ),
)

PROCESS_DELAY = PainTemplate(
    category="process_delay",
    title={"ja": "プロセスの遅延", "en": "Process delay", "ko": "프로세스 지연"},
    slots=(
        "business_impact",
        "bottleneck",
        "current_lead_time",
        "owner",
        "normal_lead_time",
        "process_step",
    ),
    questions=_by_lang(
        {
            "business_impact": _q(
                "この遅延による業務への影響は何ですか？",
                "遅延による業務影響が不明です。",
                "What business impact does this delay cause?",
                "The business impact is not yet understood.",
            ),
            "bottleneck": _q(
                "どの工程がボトルネックになっていますか？",
                "ボトルネックが特定できていません。",
                "Which step is the bottleneck?",
                "The bottleneck is not identified.",
            ),
            "current_lead_time": _q(
                "現在、全体でどのくらいの時間がかかっていますか？",
                "現状のリードタイムが不明です。",
                "How long does it currently take end to end?",
                "The current lead time is unknown.",
            ),
            "owner": _q(
                "このプロセスは、どなたが管理されていますか？",
                "管理者が不明です。",
                "Who owns this process?",
                "The owner is unclear.",
            ),
            "normal_lead_time": _q(
                "本来はどのくらいの時間で完了すべきものですか？",
                "本来のリードタイムが不明です。",
                "How long should it normally take?",
                "The normal lead time is unknown.",
            ),
            "process_step": _q(
                "この遅延はどの工程で発生していますか？",
                "発生工程が不明です。",
                "At which step does the delay occur?",
                "The process step is unknown.",
            ),
        }
    ),
)

UNCLEAR_OWNERSHIP = PainTemplate(
    category="unclear_ownership",
    title={"ja": "責任分担が不明確", "en": "Unclear ownership", "ko": "불명확한 책임자"},
    slots=(
        "owner",
        "business_impact",
        "decision_process",
        "stakeholders",
        "escalation_path",
        "current_status",
    ),
    questions=_by_lang(
        {
            "owner": _q(
                "この件は、どなたが最終責任者でしょうか？",
                "最終責任者が不明です。",
                "Who is the ultimate owner of this?",
                "The owner is unclear.",
            ),
            "business_impact": _q(
                "責任が不明確なことで、どのような支障が出ていますか？",
                "業務影響が把握できていません。",
                "What impact results from the unclear ownership?",
                "The business impact is not yet understood.",
            ),
            "decision_process": _q(
                "この件の意思決定はどのように行われていますか？",
                "意思決定プロセスが不明です。",
                "How are decisions made on this?",
                "The decision process is unclear.",
            ),
            "stakeholders": _q(
                "関係している部署や担当者はどなたですか？",
                "関係者が整理できていません。",
                "Who are the stakeholders involved?",
                "The stakeholders are not identified.",
            ),
            "escalation_path": _q(
                "判断に迷った場合、どこに相談されていますか？",
                "エスカレーション先が不明です。",
                "Where does this escalate when unresolved?",
                "The escalation path is unknown.",
            ),
            "current_status": _q(
                "現在、この件はどのような状況でしょうか？",
                "現状が把握できていません。",
                "What is the current status of this?",
                "The current status is unclear.",
            ),
        }
    ),
)

NONFUNCTIONAL_REQUIREMENT = PainTemplate(
    category="nonfunctional_requirement",
    title={
        "ja": "非機能要件が未定義",
        "en": "Non-functional requirement is undefined",
        "ko": "비기능 요구사항 미정의",
    },
    slots=("quality_attribute", "measurable_target", "scope_load", "measurement_method", "owner"),
    questions=_by_lang(
        {
            "quality_attribute": _q(
                "この要件で重視する非機能特性は何でしょうか？",
                "対象となる非機能特性が明確ではありません。",
                "Which non-functional quality attribute matters here?",
                "The relevant quality attribute is not defined.",
            ),
            "measurable_target": _q(
                "達成すべき数値目標はどの程度でしょうか？",
                "検証可能な目標値がありません。",
                "What measurable target must the solution meet?",
                "A verifiable target has not been stated.",
            ),
            "scope_load": _q(
                "どの利用範囲や負荷条件を想定していますか？",
                "適用範囲と負荷条件が不明です。",
                "What scope and load profile should this cover?",
                "The applicable scope and load profile are unknown.",
            ),
            "measurement_method": _q(
                "達成をどのように測定・確認しますか？",
                "測定方法が定義されていません。",
                "How will achievement be measured and verified?",
                "The measurement method is undefined.",
            ),
            "owner": _q(
                "この非機能要件の承認責任者はどなたですか？",
                "要件の承認責任者が不明です。",
                "Who owns approval of this non-functional requirement?",
                "The approval owner is unclear.",
            ),
        }
    ),
)

ARCHITECTURE_CONSTRAINT = PainTemplate(
    category="architecture_constraint",
    title={"ja": "アーキテクチャ制約", "en": "Architecture constraint", "ko": "아키텍처 제약"},
    slots=(
        "constraint",
        "rationale_source",
        "affected_systems",
        "decision_authority",
        "alternatives",
        "exception_path",
    ),
    questions=_by_lang(
        {
            "constraint": _q(
                "具体的な制約は何でしょうか？",
                "制約自体が明確ではありません。",
                "What exactly is the architecture constraint?",
                "The constraint itself is unclear.",
            ),
            "rationale_source": _q(
                "この制約の根拠や出所は何でしょうか？",
                "制約の根拠が確認できていません。",
                "What is the rationale or source for this constraint?",
                "The source of the constraint is unverified.",
            ),
            "affected_systems": _q(
                "どのシステムや範囲が影響を受けますか？",
                "影響対象が特定されていません。",
                "Which systems or scope are affected?",
                "The affected scope is not identified.",
            ),
            "decision_authority": _q(
                "この制約を承認・変更できるのは誰ですか？",
                "意思決定権限が不明です。",
                "Who can approve or change this constraint?",
                "The decision authority is unclear.",
            ),
            "alternatives": _q(
                "検討済みの代替案はありますか？",
                "代替案が確認できていません。",
                "Which alternatives have been considered?",
                "No considered alternatives are recorded.",
            ),
            "exception_path": _q(
                "例外が必要な場合の申請経路は何ですか？",
                "例外手続きが不明です。",
                "What is the exception path if this constraint cannot be met?",
                "The exception path is unknown.",
            ),
        }
    ),
)

SECURITY_CONTROL_GAP = PainTemplate(
    category="security_control_gap",
    title={"ja": "セキュリティ統制の不足", "en": "Security control gap", "ko": "보안 통제 공백"},
    slots=(
        "affected_asset_data",
        "threat_control_gap",
        "business_impact",
        "owner",
        "compliance_source",
        "target_date",
    ),
    questions=_by_lang(
        {
            "affected_asset_data": _q(
                "どの資産やデータが対象ですか？",
                "保護対象が特定されていません。",
                "Which assets or data are affected?",
                "The protected assets or data are not identified.",
            ),
            "threat_control_gap": _q(
                "どの脅威に対する、どの統制が不足していますか？",
                "脅威と統制の不足が明確ではありません。",
                "Which threat and missing control does this concern?",
                "The threat and control gap are unclear.",
            ),
            "business_impact": _q(
                "この不足が事業に与える影響は何ですか？",
                "事業影響が把握できていません。",
                "What business impact could this control gap cause?",
                "The business impact is not understood.",
            ),
            "owner": _q(
                "是正対応の責任者はどなたですか？",
                "是正責任者が不明です。",
                "Who owns remediation?",
                "The remediation owner is unclear.",
            ),
            "compliance_source": _q(
                "根拠となる規程や法令は何ですか？",
                "統制要求の根拠が不明です。",
                "Which policy, standard, or regulation requires this control?",
                "The compliance source is unknown.",
            ),
            "target_date": _q(
                "いつまでに是正する必要がありますか？",
                "是正期限が不明です。",
                "By when must this be remediated?",
                "The remediation deadline is unknown.",
            ),
        }
    ),
)

DEPENDENCY_BLOCKER = PainTemplate(
    category="dependency_blocker",
    title={"ja": "依存関係によるブロッカー", "en": "Dependency blocker", "ko": "의존성 차단 요인"},
    slots=(
        "provider",
        "consumer",
        "deliverable_interface",
        "needed_by",
        "owner",
        "current_status",
        "fallback",
    ),
    questions=_by_lang(
        {
            "provider": _q(
                "依存先のチームやシステムはどこですか？",
                "依存先が特定されていません。",
                "Which team or system is providing the dependency?",
                "The dependency provider is unidentified.",
            ),
            "consumer": _q(
                "この依存物を必要としている側はどこですか？",
                "依存する側が不明です。",
                "Which team or system consumes this dependency?",
                "The dependency consumer is unclear.",
            ),
            "deliverable_interface": _q(
                "必要な成果物やインターフェースは何ですか？",
                "必要な提供物が明確ではありません。",
                "What deliverable or interface is required?",
                "The required deliverable is unclear.",
            ),
            "needed_by": _q(
                "いつまでに必要ですか？",
                "必要期限が不明です。",
                "By when is it needed?",
                "The needed-by date is unknown.",
            ),
            "owner": _q(
                "この依存関係の解消を追う責任者は誰ですか？",
                "フォロー責任者が不明です。",
                "Who owns following up this dependency?",
                "The follow-up owner is unclear.",
            ),
            "current_status": _q(
                "現在の提供状況はどうなっていますか？",
                "現在の状態が不明です。",
                "What is the current delivery status?",
                "The current status is unknown.",
            ),
            "fallback": _q(
                "間に合わない場合の代替策はありますか？",
                "代替策が確認できていません。",
                "What is the fallback if the dependency is late?",
                "No fallback has been identified.",
            ),
        }
    ),
)

TEMPLATES: dict[str, PainTemplate] = {
    t.category: t
    for t in (
        DATA_MISMATCH,
        MANUAL_WORK,
        INTEGRATION_FAILURE,
        VAGUE_REQUIREMENT,
        PROCESS_DELAY,
        UNCLEAR_OWNERSHIP,
        NONFUNCTIONAL_REQUIREMENT,
        ARCHITECTURE_CONSTRAINT,
        SECURITY_CONTROL_GAP,
        DEPENDENCY_BLOCKER,
    )
}

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(TEMPLATES.keys())

# Deterministic projection from template slots into reviewable issue fields.
ISSUE_FIELD_MAPS: dict[str, dict[str, tuple[str, ...]]] = {
    "data_mismatch": {
        "problem": ("affected_scope",),
        "impact": ("business_impact",),
        "systems": ("source_of_truth",),
        "scope": ("affected_scope",),
        "owner": ("owner",),
        "workaround": ("correction_process",),
        "acceptance": (),
    },
    "integration_failure": {
        "problem": ("failure_point", "error_signal"),
        "impact": ("business_impact",),
        "systems": ("source_system", "target_system"),
        "scope": ("failure_point",),
        "owner": ("owner",),
        "workaround": ("retry_behavior",),
        "acceptance": (),
    },
    "manual_work": {
        "problem": ("reason_manual",),
        "impact": ("business_impact", "time_cost", "volume", "frequency"),
        "systems": (),
        "scope": ("volume",),
        "owner": ("owner",),
        "workaround": ("reason_manual",),
        "acceptance": ("success_condition",),
    },
    "process_delay": {
        "problem": ("bottleneck", "process_step"),
        "impact": ("business_impact", "current_lead_time", "normal_lead_time"),
        "systems": (),
        "scope": ("process_step",),
        "owner": ("owner",),
        "workaround": (),
        "acceptance": ("normal_lead_time",),
    },
    "vague_requirement": {
        "problem": ("current_problem",),
        "impact": ("business_impact",),
        "systems": (),
        "scope": (),
        "owner": ("owner",),
        "workaround": (),
        "acceptance": ("expected_behavior", "acceptance_criteria"),
    },
    "unclear_ownership": {
        "problem": ("decision_process", "current_status"),
        "impact": ("business_impact",),
        "systems": (),
        "scope": ("stakeholders",),
        "owner": ("owner",),
        "workaround": ("escalation_path",),
        "acceptance": (),
    },
    "nonfunctional_requirement": {
        "problem": ("quality_attribute", "scope_load"),
        "impact": (),
        "systems": (),
        "scope": ("scope_load",),
        "owner": ("owner",),
        "workaround": (),
        "acceptance": ("measurable_target", "measurement_method"),
    },
    "architecture_constraint": {
        "problem": ("constraint", "rationale_source"),
        "impact": (),
        "systems": ("affected_systems",),
        "scope": ("affected_systems",),
        "owner": ("decision_authority",),
        "workaround": ("alternatives", "exception_path"),
        "acceptance": (),
    },
    "security_control_gap": {
        "problem": ("threat_control_gap",),
        "impact": ("business_impact",),
        "systems": ("affected_asset_data",),
        "scope": ("affected_asset_data",),
        "owner": ("owner",),
        "workaround": (),
        "acceptance": ("compliance_source", "target_date"),
    },
    "dependency_blocker": {
        "problem": ("deliverable_interface", "current_status"),
        "impact": ("needed_by",),
        "systems": ("provider", "consumer"),
        "scope": ("deliverable_interface",),
        "owner": ("owner",),
        "workaround": ("fallback",),
        "acceptance": ("needed_by",),
    },
}

ISSUE_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "data_mismatch": ("source_of_truth", "business_impact", "owner", "affected_scope"),
    "integration_failure": (
        "business_impact",
        "failure_point",
        "owner",
        "source_system",
        "target_system",
    ),
    "manual_work": ("business_impact", "frequency", "owner", "success_condition"),
    "process_delay": ("business_impact", "process_step", "current_lead_time", "owner"),
    "vague_requirement": (
        "current_problem",
        "expected_behavior",
        "acceptance_criteria",
        "owner",
    ),
    "unclear_ownership": (
        "business_impact",
        "stakeholders",
        "decision_process",
        "escalation_path",
        "current_status",
    ),
    "nonfunctional_requirement": (
        "quality_attribute",
        "measurable_target",
        "scope_load",
        "measurement_method",
        "owner",
    ),
    "architecture_constraint": (
        "constraint",
        "rationale_source",
        "affected_systems",
        "decision_authority",
    ),
    "security_control_gap": (
        "affected_asset_data",
        "threat_control_gap",
        "business_impact",
        "owner",
        "compliance_source",
        "target_date",
    ),
    "dependency_blocker": (
        "provider",
        "consumer",
        "deliverable_interface",
        "needed_by",
        "owner",
        "current_status",
    ),
}
