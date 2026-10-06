# adapters/risk_adapter.py 테스트
#
# risk_defender(이유진 파트)의 실제 함수를 그대로 호출한다 (mock 없음).
# 8명짜리 기준 집단으로 k를 손으로 계산할 수 있게 했다.
#
#   id  AGE  LOC                  OCCP               EDU             REL
#   p1  30s  Zurich, Switzerland  software engineer  Master's        single
#   p2  30s  Zurich, Switzerland  nurse              College Degree  single
#   p3  30s  Zurich, Switzerland  chef               College Degree  married
#   p4  40s  Geneva, Switzerland  software engineer  PhD             married
#   p5  30s  Geneva, Switzerland  software engineer  Master's        divorced
#   p6  20s  Berlin, Germany      software engineer  Master's        single
#   p7  50s  Berlin, Germany      nurse              HS Diploma      widowed
#   p8  30s  Munich, Germany      teacher            College Degree  married

from __future__ import annotations

import json

import pytest

from adapters.risk_adapter import (
    ExclusionReason,
    PopulationMode,
    ReferenceDataMissingError,
    ReferencePopulation,
    RiskConfig,
    RiskSession,
    load_reference_population,
)
from evaluation_v1.log_schema_v1 import ExperimentLogRecord
from normalizer.normalizer import MappingReason, MappingStatus, NormalizedObservation
from risk_defender.demo import DEFAULT_DATA_PATH, DEFAULT_SYNTH_PATH
from risk_defender.loader import PersonaRecord

ROWS = [
    ("p1", "30s", "Zurich, Switzerland", "software engineer", "Master's", "single"),
    ("p2", "30s", "Zurich, Switzerland", "nurse", "College Degree", "single"),
    ("p3", "30s", "Zurich, Switzerland", "chef", "College Degree", "married"),
    ("p4", "40s", "Geneva, Switzerland", "software engineer", "PhD", "married"),
    ("p5", "30s", "Geneva, Switzerland", "software engineer", "Master's", "divorced"),
    ("p6", "20s", "Berlin, Germany", "software engineer", "Master's", "single"),
    ("p7", "50s", "Berlin, Germany", "nurse", "HS Diploma", "widowed"),
    ("p8", "30s", "Munich, Germany", "teacher", "College Degree", "married"),
]


@pytest.fixture
def population():
    records = [
        PersonaRecord(person_id=pid, username=None,
                      qis={"AGE": a, "LOC": l, "OCCP": o, "EDU": e, "REL": r},
                      raw_profile={}, source_texts=[])
        for pid, a, l, o, e, r in ROWS
    ]
    return ReferencePopulation(records, PopulationMode.BASE, "test", None)


@pytest.fixture
def session(population):
    return RiskSession(population, "p1", "s1", RiskConfig(risk_threshold=3))


def mapped(attribute: str, value: str, *, oid: str = "t1-o1", confidence: int = 5) -> NormalizedObservation:
    return NormalizedObservation(
        observation_id=oid, attribute=attribute, raw_value=value, value_en=value,
        confidence=confidence, source_turn=1, status=MappingStatus.MAPPED,
        reason=MappingReason.EXACT_MATCH, canonical_value=value,
    )


def unmapped(attribute: str, oid: str, status: MappingStatus = MappingStatus.PENDING) -> NormalizedObservation:
    return NormalizedObservation(
        observation_id=oid, attribute=attribute, raw_value="?", value_en=None,
        confidence=5, source_turn=1, status=status, reason=MappingReason.AMBIGUOUS,
    )


# ──────────────────────────────────────────────────────────────
# 설정 · 판정 경계
# ──────────────────────────────────────────────────────────────

def test_k_req_is_threshold_plus_one():
    assert RiskConfig(risk_threshold=3).k_req == 4
    assert RiskConfig(risk_threshold=4).k_req == 5


@pytest.mark.parametrize("kwargs", [{"risk_threshold": 0}, {"tau_commit": 0}, {"tau_commit": 6}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)


@pytest.mark.parametrize("k_after, risky", [(3, True), (4, False), (1, True)])
def test_risk_and_evaluation_use_the_same_boundary(k_after, risky):
    """risk_defender 위험(k<=3)과 evaluation 위반(1<=k<k_req)이 같은 k에서 갈려야 한다."""
    config = RiskConfig(risk_threshold=3)
    record = ExperimentLogRecord(
        experiment_id="e", target_id="p1", turn=1, seed_id="s", objective="O1", scenario_id="S1",
        target_qi="LOC", k_run_before=300, k_run_after=k_after, k_req=config.k_req,
    )
    assert record.threshold_violation is risky
    assert (k_after <= config.risk_threshold) is risky


# ──────────────────────────────────────────────────────────────
# 선별
# ──────────────────────────────────────────────────────────────

def test_initial_k_is_population_size(session):
    assert session.k_current == 8
    assert session.committed_qis == {}


def test_select_excludes_with_reasons(population):
    session = RiskSession(population, "p1", "s1", RiskConfig(risk_threshold=3, tau_commit=4))
    selection = session.select([
        mapped("LOC", "Zurich, Switzerland", oid="a"),
        unmapped("AGE", "b"),
        mapped("SEX", "male", oid="c"),
        mapped("OCCP", "nurse", oid="d", confidence=3),
    ])

    assert selection.accepted_ids == ("a",)
    reasons = {e.observation_id: e.reason for e in selection.excluded}
    assert reasons == {
        "b": ExclusionReason.NOT_MAPPED,
        "c": ExclusionReason.OUT_OF_RISK_SCOPE,
        "d": ExclusionReason.BELOW_TAU_COMMIT,
    }


def test_selected_observation_carries_id_and_no_confidence(session):
    qi = session.select([mapped("LOC", "Zurich, Switzerland", oid="t3-o2")]).accepted[0]
    assert (qi.qi_type, qi.value, qi.source, qi.confidence) == ("LOC", "Zurich, Switzerland", "t3-o2", None)


# ──────────────────────────────────────────────────────────────
# project · defend: State를 바꾸지 않는다
# ──────────────────────────────────────────────────────────────

def test_project_does_not_change_state(session):
    projection = session.project([mapped("LOC", "Zurich, Switzerland")])

    assert (projection.k_before, projection.k_after, projection.risky) == (8, 3, True)
    assert session.k_current == 8
    assert session.committed_qis == {}


def test_project_without_usable_observations(session):
    projection = session.project([unmapped("AGE", "x")])
    assert (projection.k_before, projection.k_after) == (8, 8)


def test_zero_match_is_risky(session):
    assert session.project([mapped("OCCP", "astronaut")]).risky is True


# risk_defender 수정본(2026-10-04)의 판정: k > 5 허용 / 3 < k <= 5 일반화 시도 / k <= 3 바로 억제

def test_defend_allows_disclosure_above_generalize_threshold(session):
    outcome = session.defend([mapped("AGE", "adult", oid="o1")])        # 20~60대 전원 → k = 8

    assert outcome.action == "allow"
    assert outcome.allowed_ids == ("o1",)
    assert (outcome.k_raw, outcome.k_final) == (8, 8)


def test_defend_keeps_value_when_no_generalization_exists_but_k_is_above_suppress_threshold(session):
    """직업은 일반화 단계가 없다. k = 4 (3 초과)이므로 값은 그대로 공개된다."""
    outcome = session.defend([mapped("OCCP", "software engineer", oid="o1")])

    assert outcome.allowed_ids == ("o1",)
    assert (outcome.k_raw, outcome.k_final) == (4, 4)
    assert outcome.intervened is False


def test_defend_generalizes_when_k_is_between_thresholds(session):
    outcome = session.defend([mapped("AGE", "30s", oid="o1")])          # 그대로면 k = 5 → adult면 8

    assert outcome.action == "generalize"
    assert outcome.generalized == (("o1", "AGE", "30s", "adult"),)
    assert (outcome.k_raw, outcome.k_final) == (5, 8)
    assert session.committed_qis == {}           # defend도 State를 바꾸지 않음


def test_defend_suppresses_directly_at_or_below_suppress_threshold(session):
    """취리히는 k = 3. 국가로 일반화하면 5가 되지만, 수정본은 k <= 3이면 일반화를 시도하지 않고 억제한다."""
    outcome = session.defend([mapped("LOC", "Zurich, Switzerland", oid="o1")])

    assert outcome.action == "suppress"
    assert outcome.suppressed_ids == ("o1",)
    assert outcome.generalized == ()
    assert (outcome.k_raw, outcome.k_final) == (3, 8)


def test_generalize_threshold_must_not_be_below_risk_threshold():
    with pytest.raises(ValueError):
        RiskConfig(risk_threshold=5, generalize_threshold=4)


def test_defend_suppresses_when_no_safe_generalization(session):
    session.commit([mapped("OCCP", "software engineer")])                # k = 4
    outcome = session.defend([mapped("REL", "single", oid="o3")])       # single → 2, not_partnered → 3

    assert outcome.action == "suppress"
    assert outcome.suppressed_ids == ("o3",)


def test_defend_with_nothing_usable_does_not_call_risk_defender(session):
    outcome = session.defend([unmapped("AGE", "x")])
    assert outcome.action == "allow"
    assert outcome.defense_log == ()


# ──────────────────────────────────────────────────────────────
# commit: 실제 공개분만 State에 반영
# ──────────────────────────────────────────────────────────────

def test_commit_updates_state_and_k(session):
    outcome = session.commit([mapped("LOC", "Switzerland", oid="o1")])

    assert (outcome.k_before, outcome.k_after) == (8, 5)
    assert outcome.committed_ids == ("o1",)
    assert session.committed_qis == {"LOC": "Switzerland"}
    assert session.k_current == 5


def test_commit_more_specific_value_refines(session):
    session.commit([mapped("LOC", "Switzerland")])
    outcome = session.commit([mapped("LOC", "Zurich, Switzerland")])

    assert outcome.k_after == 3
    assert session.committed_qis == {"LOC": "Zurich, Switzerland"}


def test_commit_conflicting_value_keeps_existing(session):
    session.commit([mapped("LOC", "Zurich, Switzerland")])
    outcome = session.commit([mapped("LOC", "Geneva, Switzerland")])

    assert outcome.new_conflicts == 1
    assert outcome.k_after == 3
    assert session.committed_qis == {"LOC": "Zurich, Switzerland"}


def test_commit_nothing_usable_keeps_state(session):
    outcome = session.commit([unmapped("AGE", "x")])
    assert (outcome.k_before, outcome.k_after) == (8, 8)
    assert outcome.committed_ids == ()


def test_log_dicts_are_serializable(session):
    obs = [mapped("LOC", "Zurich, Switzerland"), unmapped("AGE", "x")]
    for item in (session.project(obs), session.defend(obs), session.commit(obs)):
        json.dumps(item.to_log_dict(), ensure_ascii=False)


# ──────────────────────────────────────────────────────────────
# 기준 집단 적재
# ──────────────────────────────────────────────────────────────

def _write_profiles(path, ids):
    path.write_text(json.dumps({pid: {"age": 30, "city_country": "Zurich, Switzerland",
                                      "occupation": "nurse", "education": "Masters",
                                      "relationship_status": "single"} for pid in ids}),
                    encoding="utf-8")


def test_expanded_mode_requires_synthetic_file(tmp_path):
    base = tmp_path / "base.json"
    _write_profiles(base, ["pers1"])
    with pytest.raises(ReferenceDataMissingError) as exc:
        load_reference_population(PopulationMode.EXPANDED, base_path=base, synth_path=tmp_path / "none.json")
    assert "python -m adapters.prepare_reference" in str(exc.value)


def test_base_and_expanded_loading(tmp_path):
    base, synth = tmp_path / "base.json", tmp_path / "synth.json"
    _write_profiles(base, ["pers1", "pers2"])
    _write_profiles(synth, ["pers3", "pers4", "pers5"])

    assert load_reference_population(PopulationMode.BASE, base_path=base, synth_path=synth).size == 2
    expanded = load_reference_population(PopulationMode.EXPANDED, base_path=base, synth_path=synth)
    assert (expanded.size, expanded.mode) == (5, PopulationMode.EXPANDED)


@pytest.mark.skipif(not (DEFAULT_DATA_PATH.exists() and DEFAULT_SYNTH_PATH.exists()),
                    reason="기준 집단 데이터 미준비 (python -m adapters.prepare_reference)")
def test_real_expanded_population():
    population = load_reference_population()
    assert population.size == 300_000
    session = RiskSession(population, "pers1", "real")
    assert session.k_current == 300_000


# ──────────────────────────────────────────────────────────────
# orchestrator가 쓰는 공통 메서드 (RiskBackend)
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "attribute, existing, candidate, expected",
    [
        ("LOC", "Switzerland", "Switzerland", "SAME"),
        ("LOC", "Switzerland", "Zurich, Switzerland", "DESCENDANT"),
        ("LOC", "Zurich, Switzerland", "Switzerland", "ANCESTOR"),
        ("OCCP", "nurse", "chef", "INCOMPATIBLE"),
    ],
)
def test_relation(session, attribute, existing, candidate, expected):
    assert session.relation(attribute, existing, candidate) == expected


def test_fresh_session_shares_population_but_not_state(session):
    session.commit([mapped("LOC", "Switzerland")])
    other = session.fresh()
    assert (session.k_current, other.k_current) == (5, 8)
    assert other.committed_qis == {} and other.config == session.config


def test_legacy_backend_defaults(session):
    assert session.backend_name == "risk_defender"
    assert session.refinable_qis is None
    assert session.display_value("LOC", "Switzerland") == "Switzerland"
