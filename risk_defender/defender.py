# 위험한 QI가 들어왔을 때 방어 동작을 수행하는 파일

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .loader import PersonaRecord
from .risk import ProjectedRiskResult, evaluate_projected_risk
from .schema import PrivacyState, QIObservation


@dataclass(frozen=True)
class DefenseDecision:
    action: str
    projected: ProjectedRiskResult
    safe_qis: list[QIObservation]
    generalized_qis: list[dict[str, Any]]
    suppressed_qis: list[QIObservation]
    final_state: PrivacyState
    final_k: int
    final_risk: float
    log: list[dict[str, Any]]


def defend_candidate_qis(
    population: list[PersonaRecord],
    current_state: PrivacyState,
    candidate_qis: list[QIObservation],
    *,
    generalize_threshold: int = 5,  # k ≤ 이 값이면 일반화 시도
    suppress_threshold: int = 3,    # k ≤ 이 값이면 억제
    min_confidence: float = 0.5,
) -> DefenseDecision:
    projected = evaluate_projected_risk(
        population,
        current_state,
        candidate_qis,
        threshold=generalize_threshold,
        min_confidence=min_confidence,
    )
    if not projected.risky:
        return DefenseDecision(
            action="allow",
            projected=projected,
            safe_qis=candidate_qis,
            generalized_qis=[],
            suppressed_qis=[],
            final_state=projected.state_after,
            final_k=projected.k_after,
            final_risk=projected.risk_after,
            log=[{"action": "allow", "k_after": projected.k_after}],
        )

    safe_qis: list[QIObservation] = []
    generalized_qis: list[dict[str, Any]] = []
    suppressed_qis: list[QIObservation] = []
    defense_log: list[dict[str, Any]] = [
        {
            "action": "defense_started",
            "reason": "threshold_crossed",
            "k_after_raw": projected.k_after,
            "generalize_threshold": generalize_threshold,
            "suppress_threshold": suppress_threshold,
        }
    ]
    working_state = current_state

    for qi in candidate_qis:
        defended_qi = _find_safe_generalization(
            population,
            working_state,
            qi,
            generalize_threshold=generalize_threshold,
            suppress_threshold=suppress_threshold,
            min_confidence=min_confidence,
        )
        if defended_qi is None:
            suppressed_qis.append(qi)
            defense_log.append(
                {
                    "action": "suppress",
                    "qi_type": qi.qi_type,
                    "value": qi.value,
                    "reason": "k_below_suppress_threshold",
                }
            )
            continue

        if defended_qi.value != qi.value:
            generalized_qis.append(
                {
                    "qi_type": qi.qi_type,
                    "from": qi.value,
                    "to": defended_qi.value,
                }
            )
            defense_log.append(
                {
                    "action": "generalize",
                    "qi_type": qi.qi_type,
                    "from": qi.value,
                    "to": defended_qi.value,
                }
            )
        else:
            safe_qis.append(qi)

        step = evaluate_projected_risk(
            population,
            working_state,
            [defended_qi],
            threshold=generalize_threshold,
            min_confidence=min_confidence,
        )
        working_state = step.state_after

    final_projection = evaluate_projected_risk(
        population,
        current_state,
        [*safe_qis, *[QIObservation(item["qi_type"], item["to"]) for item in generalized_qis]],
        threshold=generalize_threshold,
        min_confidence=min_confidence,
    )
    final_action = "suppress" if suppressed_qis and not safe_qis and not generalized_qis else "generalize"
    return DefenseDecision(
        action=final_action,
        projected=projected,
        safe_qis=safe_qis,
        generalized_qis=generalized_qis,
        suppressed_qis=suppressed_qis,
        final_state=working_state,
        final_k=final_projection.k_after,
        final_risk=final_projection.risk_after,
        log=defense_log,
    )


def _find_safe_generalization(
    population: list[PersonaRecord],
    state: PrivacyState,
    qi: QIObservation,
    *,
    generalize_threshold: int,
    suppress_threshold: int,
    min_confidence: float,
) -> QIObservation | None:
    # raw QI 추가 시 k가 suppress_threshold 이하면 일반화 없이 바로 억제
    raw_projected = evaluate_projected_risk(
        population,
        state,
        [qi],
        threshold=suppress_threshold,
        min_confidence=min_confidence,
    )
    if raw_projected.k_after <= suppress_threshold:
        return None

    # suppress_threshold < k ≤ generalize_threshold → 일반화 시도 (가장 구체적인 것부터)
    candidates = [qi, *generalization_chain(qi)]
    for candidate in candidates:
        projected = evaluate_projected_risk(
            population,
            state,
            [candidate],
            threshold=generalize_threshold,
            min_confidence=min_confidence,
        )
        if projected.k_after > generalize_threshold:
            return candidate

    # generalize_threshold 달성 불가 시, suppress_threshold 초과 여부로 억제 결정
    for candidate in candidates:
        projected = evaluate_projected_risk(
            population,
            state,
            [candidate],
            threshold=suppress_threshold,
            min_confidence=min_confidence,
        )
        if projected.k_after > suppress_threshold:
            return candidate

    return None  # 어떤 일반화도 suppress_threshold 초과 불가 → 억제


def generalization_chain(qi: QIObservation) -> list[QIObservation]:
    value = str(qi.value)

    if qi.qi_type == "LOC":
        return [
            QIObservation(qi.qi_type, parent, qi.source_turn, qi.confidence, qi.source)
            for parent in _location_generalizations(value)
        ]

    if qi.qi_type == "AGE":
        if value in {"teens"}:
            return [QIObservation(qi.qi_type, "young", qi.source_turn, qi.confidence, qi.source)]
        if value in {"20s", "30s", "40s", "50s", "60s"}:
            return [QIObservation(qi.qi_type, "adult", qi.source_turn, qi.confidence, qi.source)]
        if value in {"70s", "80s", "90s"}:
            return [QIObservation(qi.qi_type, "senior", qi.source_turn, qi.confidence, qi.source)]

    if qi.qi_type == "EDU":
        if value in {"Master's", "PhD", "College Degree"}:
            return [QIObservation(qi.qi_type, "higher_education", qi.source_turn, qi.confidence, qi.source)]
        if value in {"HS Diploma", "In College/HS Diploma"}:
            return [QIObservation(qi.qi_type, "non_advanced_degree", qi.source_turn, qi.confidence, qi.source)]

    if qi.qi_type == "REL":
        if value in {"married", "engaged", "in a relationship"}:
            return [QIObservation(qi.qi_type, "partnered", qi.source_turn, qi.confidence, qi.source)]
        if value in {"single", "divorced", "widowed"}:
            return [QIObservation(qi.qi_type, "not_partnered", qi.source_turn, qi.confidence, qi.source)]

    return []


def _location_generalizations(value: str) -> list[str]:
    generalizations: list[str] = []
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) >= 2:
        generalizations.append(parts[-1])

    deduplicated: list[str] = []
    seen_values: set[str] = set()
    for item in generalizations:
        normalized = item.casefold()
        if normalized not in seen_values:
            deduplicated.append(item)
            seen_values.add(normalized)
    return deduplicated
