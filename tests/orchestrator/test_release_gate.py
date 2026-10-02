# orchestrator/release_gate.py 테스트 — 조건별 공개 경로와 하한 확인
# risk_defender 판정·일반화는 실제 것을 쓴다. k는 sample_data.py의 8명 표로 계산한다.

from __future__ import annotations

import pytest

from adapters.risk_adapter import RiskConfig, RiskSession
from normalizer.normalizer import Normalizer
from normalizer.vocabulary import ReferenceVocabulary
from orchestrator.release_gate import (
    Condition,
    GateConfig,
    RefusalReason,
    ReleaseGate,
    ReleasePath,
    floor_satisfied,
)
from orchestrator.rewriter import ResponseRewriter
from qi_extractor.extractor import QIExtractor
from tests.orchestrator.sample_data import (
    RAW_T1,
    RAW_T2,
    REWRITES,
    TARGET_ID,
    RoleClient,
    make_population,
)

QUERY = "변형 질문"


def build(client: RoleClient, **gate_config):
    population = make_population()
    normalizer = Normalizer(ReferenceVocabulary.from_population(population.records))
    extractor = QIExtractor(client)
    gate = ReleaseGate(extractor, normalizer, ResponseRewriter(client), GateConfig(**gate_config))
    session = RiskSession(population, TARGET_ID, "s1", RiskConfig(risk_threshold=3))
    return gate, session, extractor, normalizer


def raw_observations(extractor: QIExtractor, text: str):
    return extractor.extract(QUERY, text, source_turn=1).observations


_EXTRACT = object()   # raw 인자를 주지 않으면 여기서 추출한다 (None은 '추출 실패'를 뜻하므로 구분)


def release(gate, session, extractor, condition, text, raw=_EXTRACT):
    if raw is _EXTRACT:
        raw = raw_observations(extractor, text)
    return gate.release(condition, session, query=QUERY, raw_response=text, raw_observations=raw, turn=1)


# ── 하한 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "k_before, k_after, k_floor, ok",
    [(8, 5, 4, True), (8, 3, 4, False), (3, 3, 3, True), (3, 2, 3, False), (5, 0, 4, False), (0, 0, 0, True)],
)
def test_floor_satisfied(k_before, k_after, k_floor, ok):
    assert floor_satisfied(k_before, k_after, k_floor) is ok


# ── C0 ────────────────────────────────────────────────────

def test_c0_releases_raw_candidate_unchanged():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C0, RAW_T1)

    assert decision.path is ReleasePath.PASSTHROUGH
    assert decision.released_response == RAW_T1
    assert [o.canonical_value for o in decision.released_observations] == ["Zurich, Switzerland"]
    assert (decision.k_before, decision.k_raw_projected, decision.k_released_projected) == (8, 3, 3)
    assert not decision.defender_enabled and not decision.intervened
    assert client.count("rewriter") == 0


# ── C1 ────────────────────────────────────────────────────

def test_c1_allows_response_that_stays_above_threshold():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, "이 사용자는 스위스에 거주합니다.")

    assert decision.path is ReleasePath.ALLOWED
    assert decision.k_released_projected == 5                 # 스위스 5명
    assert client.count("rewriter") == 0


def test_c1_generalizes_then_releases_rewritten_response():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_T1)

    assert decision.path is ReleasePath.REWRITTEN
    assert decision.released_response == REWRITES[RAW_T1]
    assert [o.canonical_value for o in decision.released_observations] == ["Switzerland"]
    assert (decision.k_raw_projected, decision.k_floor, decision.k_released_projected) == (3, 4, 5)
    assert decision.generalized and not decision.suppressed and decision.floor_satisfied
    assert "바꿀 표현: Switzerland" in client.prompts_of("rewriter")[0]   # risk_defender가 정한 값만 전달


def test_c1_suppresses_qi_without_generalization():
    """OCCP는 risk_defender에 일반화 단계가 없어 억제된다. State: 스위스(5명) → 30대(4명)는 통과, 직업(2명)은 억제."""
    client = RoleClient([])
    gate, session, extractor, normalizer = build(client)
    session.commit(normalizer.normalize_all(raw_observations(extractor, "이 사용자는 스위스에 거주합니다.")))

    decision = release(gate, session, extractor, Condition.C1, RAW_T2)

    assert decision.path is ReleasePath.REWRITTEN
    assert [o.attribute for o in decision.released_observations] == ["AGE"]
    assert (decision.k_before, decision.k_raw_projected, decision.k_released_projected) == (5, 2, 4)
    assert decision.suppressed and not decision.generalized
    assert "삭제 | 속성: OCCP" in client.prompts_of("rewriter")[0]


def test_gate_never_changes_state():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    release(gate, session, extractor, Condition.C1, RAW_T1)
    assert session.k_current == 8 and session.committed_qis == {}


def test_second_round_when_first_rewrite_still_leaks():
    """첫 고쳐 쓰기가 도시를 그대로 남기면 다시 판정·고쳐 쓰기를 한다."""
    outputs = iter([RAW_T1, REWRITES[RAW_T1]])
    client = RoleClient([], rewrites=lambda original: next(outputs))
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_T1)

    assert decision.path is ReleasePath.REWRITTEN
    assert [r.floor_satisfied for r in decision.rounds] == [False, True]
    assert decision.k_released_projected == 5


def test_rounds_exhausted_falls_back_to_refusal():
    client = RoleClient([], rewrites=lambda original: RAW_T1)      # 몇 번을 고쳐도 도시가 남음
    gate, session, extractor, _ = build(client, max_defense_rounds=2, refusal_text="답변 불가")
    decision = release(gate, session, extractor, Condition.C1, RAW_T1)

    assert decision.path is ReleasePath.REFUSAL
    assert decision.refusal_reason is RefusalReason.ROUNDS_EXHAUSTED
    assert decision.released_response == "답변 불가"
    assert decision.released_observations == ()
    assert decision.k_released_projected == decision.k_before == 8
    assert client.count("rewriter") == 2


def test_unusable_rewrite_falls_back_to_refusal():
    client = RoleClient([], rewrites=lambda original: "   ")
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_T1)
    assert decision.refusal_reason is RefusalReason.REWRITE_FAILED


def test_reextraction_failure_falls_back_to_refusal():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    raw = raw_observations(extractor, RAW_T1)
    client._extractor = lambda text: "JSON이 아님"                  # 이후 추출기 출력이 깨짐
    decision = release(gate, session, extractor, Condition.C1, RAW_T1, raw=raw)
    assert decision.refusal_reason is RefusalReason.REEXTRACTION_FAILED


# ── raw candidate 추출 실패 ───────────────────────────────

def test_raw_extraction_failure_is_handled_per_condition():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)

    c0 = release(gate, session, extractor, Condition.C0, RAW_T1, raw=None)
    c1 = release(gate, session, extractor, Condition.C1, RAW_T1, raw=None)

    assert (c0.path, c0.released_response, c0.released_observations) == (ReleasePath.PASSTHROUGH, RAW_T1, ())
    assert (c1.path, c1.refusal_reason) == (ReleasePath.REFUSAL, RefusalReason.RAW_EXTRACTION_FAILED)


# ── 이미 기준 아래인 State ────────────────────────────────

def test_preexisting_risk_does_not_block_but_stops_further_narrowing():
    """k가 이미 3(<k_req 4)이면 하한은 3이다. 더 좁히는 QI만 막고 응답 자체는 내보낸다 (D1: 전부 차단 금지)."""
    client = RoleClient([])
    gate, session, extractor, normalizer = build(client)
    session.commit(normalizer.normalize_all(raw_observations(extractor, RAW_T1)))     # 취리히 → k=3
    assert session.k_current == 3

    decision = release(gate, session, extractor, Condition.C1, RAW_T2)

    assert decision.k_floor == 3
    assert decision.path is ReleasePath.REWRITTEN
    assert decision.k_released_projected >= 3 and decision.floor_satisfied


# ── 지원하지 않는 조건 ────────────────────────────────────

@pytest.mark.parametrize("condition", [Condition.C2, Condition.C3])
def test_unsupported_conditions(condition):
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    with pytest.raises(ValueError):
        release(gate, session, extractor, condition, RAW_T1)
