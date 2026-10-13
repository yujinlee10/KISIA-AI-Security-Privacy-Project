from __future__ import annotations

import pytest

from risk_defender_medical.defender import defend_candidate_qis
from risk_defender_medical.loader import PersonaRecord, load_population
from risk_defender_medical.risk import calculate_k
from risk_defender_medical.schema import DEFAULT_QI_ORDER, PrivacyState, QIObservation


@pytest.fixture(scope="module")
def population() -> list[PersonaRecord]:
    return load_population()


def test_medical_population_is_reproducible(population: list[PersonaRecord]) -> None:
    assert len(population) == 1000
    assert set(population[0].qis) <= set(DEFAULT_QI_ORDER)


def test_empty_state_contains_the_full_population(population: list[PersonaRecord]) -> None:
    state = PrivacyState(target_id=population[0].person_id, session_id="test")
    assert calculate_k(population, state).k == len(population)


def test_rare_region_is_generalized(population: list[PersonaRecord]) -> None:
    target = population[0]
    region = target.qis["REGION"]
    state = PrivacyState(target_id=target.person_id, session_id="test")

    decision = defend_candidate_qis(
        population,
        state,
        [QIObservation("REGION", region, source_turn=1, confidence=1.0)],
        generalize_threshold=5,
        suppress_threshold=3,
    )

    assert decision.projected.k_after <= 5
    assert decision.action == "generalize"
    assert decision.generalized_qis[0]["from"] == region
    assert decision.final_k > decision.projected.k_after
