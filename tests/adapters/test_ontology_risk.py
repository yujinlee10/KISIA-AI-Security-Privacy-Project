# adapters/ontology_risk.py 테스트 — 온톨로지 백엔드의 State 갱신, 판정, 실행기와의 접점
# 기준 집단은 tests/ontology/conftest.py와 같은 8명이다.

from __future__ import annotations

import json

import pytest

from adapters.ontology_risk import OntologyRiskSession, build_population_index
from adapters.risk_adapter import ExclusionReason, RiskConfig
from normalizer.normalizer import MappingReason, MappingStatus, NormalizedObservation
from tests.orchestrator.sample_data import make_population


@pytest.fixture(scope="module")
def index():
    return build_population_index(make_population())


@pytest.fixture
def session(index):
    return OntologyRiskSession(index, "p1", "s1", RiskConfig(risk_threshold=3))     # k_req 4


def node(node_id: str, *, oid: str = "t1-o1", confidence: int = 5) -> NormalizedObservation:
    return NormalizedObservation(
        observation_id=oid, attribute=node_id.split(":")[0], raw_value=node_id, value_en=None,
        confidence=confidence, source_turn=1, status=MappingStatus.MAPPED,
        reason=MappingReason.EXACT_MATCH, canonical_value=node_id,
    )


def pending(attribute: str, oid: str) -> NormalizedObservation:
    return NormalizedObservation(observation_id=oid, attribute=attribute, raw_value="?", value_en=None,
                                 confidence=5, source_turn=1, status=MappingStatus.PENDING,
                                 reason=MappingReason.AMBIGUOUS)


# ── 선별 ──────────────────────────────────────────────────

def test_all_eight_qis_are_in_scope(session):
    selection = session.select([node("SEX:MALE", oid="a"), node("INC:HIGH", oid="b"), node("POB:C:DE", oid="c")])
    assert selection.accepted_ids == ("a", "b", "c") and selection.excluded == ()


def test_select_excludes_unmapped_unknown_and_low_confidence(index):
    session = OntologyRiskSession(index, "p1", "s1", RiskConfig(risk_threshold=3, tau_commit=4))
    selection = session.select([pending("AGE", "a"), node("LOC:XX:nowhere", oid="b"),
                                node("SEX:MALE", oid="c", confidence=3), node("SEX:MALE", oid="d")])
    assert selection.accepted_ids == ("d",)
    assert {e.observation_id: e.reason for e in selection.excluded} == {
        "a": ExclusionReason.NOT_MAPPED, "b": ExclusionReason.NOT_MAPPED, "c": ExclusionReason.BELOW_TAU_COMMIT}


# ── State 갱신 규칙 (D1 Stage 4) ──────────────────────────

def test_commit_new_then_refine(session):
    assert session.commit([node("LOC:C:CH")]).k_after == 5
    outcome = session.commit([node("LOC:CH:zurich")])
    assert (outcome.k_before, outcome.k_after) == (5, 3)
    assert session.committed_qis == {"LOC": "LOC:CH:zurich"}


def test_broader_value_does_not_erase_specific_one(session):
    session.commit([node("LOC:CH:zurich")])
    outcome = session.commit([node("LOC:C:CH")])
    assert outcome.k_after == 3 and session.committed_qis == {"LOC": "LOC:CH:zurich"} and outcome.new_conflicts == 0


def test_incompatible_value_is_recorded_as_conflict(session):
    session.commit([node("LOC:CH:zurich")])
    outcome = session.commit([node("LOC:CH:geneva")])
    assert outcome.new_conflicts == 1 and session.conflict_count == 1
    assert session.committed_qis == {"LOC": "LOC:CH:zurich"} and outcome.k_after == 3


def test_previously_uncounted_qis_now_narrow_k(session):
    """성별·소득·출생지는 risk_defender 백엔드에서 k에 들어가지 않았다."""
    outcome = session.commit([node("SEX:MALE", oid="a"), node("INC:HIGH", oid="b")])
    assert (outcome.k_before, outcome.k_after) == (8, 2)


def test_committed_qis_follow_qi_order(session):
    session.commit([node("POB:C:DE", oid="a"), node("AGE:10Y30", oid="b")])
    assert list(session.committed_qis) == ["AGE", "POB"]


# ── project · defend: State를 바꾸지 않는다 ───────────────

def test_project_reports_violation_without_changing_state(session):
    projection = session.project([node("LOC:CH:zurich")])
    assert (projection.k_before, projection.k_after, projection.risky) == (8, 3, True)
    assert session.k_current == 8 and session.committed_qis == {}


def test_defend_generalizes_city_to_country(session):
    """같은 입력에 risk_defender 백엔드(수정본)는 바로 억제한다. 여기서는 국가로 일반화한다."""
    outcome = session.defend([node("LOC:CH:zurich", oid="o1")])

    assert outcome.action == "generalize"
    assert outcome.generalized == (("o1", "LOC", "LOC:CH:zurich", "LOC:C:CH"),)
    assert outcome.replacement_text == {"o1": "스위스 (Switzerland)"}          # 응답에 쓸 표현
    assert (outcome.k_raw, outcome.k_final) == (3, 5)
    assert session.committed_qis == {}


def test_defend_allows_safe_and_already_known_values(session):
    session.commit([node("LOC:CH:zurich")])                                      # k 3 → 하한 3
    outcome = session.defend([node("LOC:C:CH", oid="a"), node("AGE:10Y30", oid="b")])   # 더 넓은 값 + 3명 그대로
    assert outcome.action == "allow" and outcome.allowed_ids == ("a", "b") and not outcome.intervened


def test_defend_suppresses_conflicting_value(session):
    session.commit([node("LOC:C:CH")])
    outcome = session.defend([node("LOC:DE:berlin", oid="x")])
    assert outcome.suppressed_ids == ("x",)
    assert outcome.defense_log[0]["intervention_reason"] == "CONFLICT"


def test_same_qi_twice_in_one_response_is_released_at_one_level(session):
    """응답에 '30대'와 '34세'가 함께 있으면 둘 다 정해진 공개 수준(30대)으로 맞춘다."""
    session.commit([node("LOC:C:CH")])
    outcome = session.defend([node("AGE:10Y30", oid="a"), node("AGE:Y34", oid="b")])

    assert outcome.allowed_ids == ("a",)
    assert outcome.generalized == (("b", "AGE", "AGE:Y34", "AGE:10Y30"),)
    assert outcome.k_final == 4


def test_defend_with_nothing_usable(session):
    outcome = session.defend([pending("AGE", "x")])
    assert outcome.action == "allow" and not outcome.intervened


def test_log_dicts_are_serializable(session):
    obs = [node("LOC:CH:zurich"), pending("AGE", "x")]
    for item in (session.project(obs), session.defend(obs), session.commit(obs)):
        json.dumps(item.to_log_dict(), ensure_ascii=False)


# ── 실행기와의 접점 (RiskBackend) ─────────────────────────

def test_refinable_qis_come_from_the_ontology(session):
    session.commit([node("LOC:C:CH", oid="a"), node("SEX:MALE", oid="b"), node("AGE:10Y30", oid="c"),
                    node("OCCP:2512", oid="d")])
    assert session.refinable_qis == frozenset({"LOC", "AGE"})       # 성별·ISCO 4자리는 더 내려갈 곳이 없다


def test_display_value_and_relation(session):
    assert session.display_value("LOC", "LOC:C:CH") == "스위스 (Switzerland)"
    assert session.display_value("AGE", "AGE:5Y30") == "30대 초반 (early 30s)"
    assert session.relation("LOC", "LOC:CH:zurich", "LOC:C:CH") == "ANCESTOR"
    assert session.backend_name == "ontology_d1"


def test_relation_with_a_value_outside_the_ontology_does_not_crash(session):
    """정규화되지 않은 표현(예: 'Busan')이 비교 대상으로 들어와도 오류 없이 INCOMPATIBLE로 답한다."""
    assert session.relation("LOC", "Busan", "LOC:C:CH") == "INCOMPATIBLE"
    assert session.relation("LOC", "Busan", "Busan") == "SAME"


def test_action_is_suppress_when_nothing_is_generalized(session):
    session.commit([node("LOC:C:CH", oid="s")])
    outcome = session.defend([node("AGE:10Y30", oid="a"), node("SEX:MALE", oid="b"), node("REL:SINGLE", oid="c")])
    assert outcome.generalized == () and outcome.suppressed_ids and outcome.allowed_ids
    assert outcome.action == "suppress"


def test_fresh_session_is_empty(session):
    session.commit([node("LOC:C:CH")])
    other = session.fresh()
    assert (session.k_current, other.k_current) == (5, 8) and other.committed_qis == {}
