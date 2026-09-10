from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .loader import PersonaRecord
from .schema import PrivacyState, QIObservation, update_state


@dataclass(frozen=True)
class CandidateSetResult:
    k: int
    candidate_ids: list[str]
    risk: float
    matched_qis: dict[str, Any]


@dataclass(frozen=True)
class ProjectedRiskResult:
    k_before: int
    k_after: int
    risk_before: float
    risk_after: float
    risk_gain: float
    risky: bool
    state_after: PrivacyState
    candidate_ids_before: list[str]
    candidate_ids_after: list[str]


def calculate_k(
    population: list[PersonaRecord],
    state_or_qis: PrivacyState | dict[str, Any],
) -> CandidateSetResult:
    qis = (
        state_or_qis.committed_qis()
        if isinstance(state_or_qis, PrivacyState)
        else state_or_qis
    )

    candidates = [
        persona
        for persona in population
        if _matches_all_qis(persona.qis, qis)
    ]
    k = len(candidates)
    risk = 1.0 / k if k > 0 else 0.0
    return CandidateSetResult(
        k=k,
        candidate_ids=[candidate.person_id for candidate in candidates],
        risk=risk,
        matched_qis=dict(qis),
    )


def evaluate_projected_risk(
    population: list[PersonaRecord],
    current_state: PrivacyState,
    candidate_qis: list[QIObservation],
    *,
    threshold: int,
    min_confidence: float = 0.5,
) -> ProjectedRiskResult:
    before = calculate_k(population, current_state)
    state_after = update_state(
        current_state,
        candidate_qis,
        min_confidence=min_confidence,
    )
    after = calculate_k(population, state_after)
    return ProjectedRiskResult(
        k_before=before.k,
        k_after=after.k,
        risk_before=before.risk,
        risk_after=after.risk,
        risk_gain=after.risk - before.risk,
        risky=after.k <= threshold if after.k > 0 else True,
        state_after=state_after,
        candidate_ids_before=before.candidate_ids,
        candidate_ids_after=after.candidate_ids,
    )


def _matches_all_qis(persona_qis: dict[str, Any], query_qis: dict[str, Any]) -> bool:
    for qi_type, expected in query_qis.items():
        actual = persona_qis.get(qi_type)
        if not _matches_qi(qi_type, actual, expected):
            return False
    return True


def _matches_qi(qi_type: str, actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return False

    actual_text = str(actual).strip()
    expected_text = str(expected).strip()
    if actual_text.casefold() == expected_text.casefold():
        return True

    if qi_type == "location":
        return expected_text.casefold() in _location_ancestors(actual_text)

    if qi_type == "age":
        if expected_text == "adult":
            return actual_text in {"20s", "30s", "40s", "50s", "60s"}
        if expected_text == "senior":
            return actual_text in {"70s", "80s", "90s"}
        if expected_text == "young":
            return actual_text == "teens"

    if qi_type == "education":
        if expected_text == "higher_education":
            return actual_text in {"College Degree", "Master's", "PhD"}
        if expected_text == "non_advanced_degree":
            return actual_text in {"HS Diploma", "In College/HS Diploma"}

    if qi_type == "relationship_status":
        if expected_text == "partnered":
            return actual_text in {"married", "engaged", "in a relationship"}
        if expected_text == "not_partnered":
            return actual_text in {"single", "divorced", "widowed"}

    return False


def _location_ancestors(value: str) -> set[str]:
    ancestors: set[str] = set()
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) >= 2:
        ancestors.add(parts[-1].casefold())
    return ancestors
