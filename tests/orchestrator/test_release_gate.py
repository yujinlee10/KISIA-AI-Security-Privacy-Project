# orchestrator/release_gate.py 테스트 — 조건별 공개 경로와 하한 확인 (risk_defender 백엔드)
# risk_defender 판정·일반화는 실제 것을 쓴다. k는 sample_data.py의 8명 표로 계산한다.
#
# risk_defender 수정본의 판정 (기준: 억제 3, 일반화 5 → k_req 4)
#   k > 5 허용 / 3 < k <= 5 일반화 시도, 안 되면 그대로 / k <= 3 바로 억제

from __future__ import annotations

import pytest

from adapters.risk_adapter import RiskConfig, RiskSession
from normalizer.normalizer import MappingReason, Normalizer
from normalizer.vocabulary import ReferenceVocabulary
from orchestrator.release_gate import (
    Arm,
    Condition,
    DefenderMode,
    GateConfig,
    RefusalReason,
    ReleaseGate,
    ReleasePath,
    floor_satisfied,
)
from orchestrator.rewriter import ResponseRewriter
from qi_extractor.extractor import QIExtractor
from tests.orchestrator.sample_data import (
    RAW_AGE,
    RAW_T1,
    RAW_T2,
    RAW_ZURICH,
    TARGET_ID,
    RoleClient,
    make_population,
)

QUERY = "변형 질문"
_EXTRACT = object()   # raw 인자를 주지 않으면 여기서 추출한다 (None은 '추출 실패'를 뜻하므로 구분)


def build(client: RoleClient, **gate_config):
    population = make_population()
    normalizer = Normalizer(ReferenceVocabulary.from_population(population.records))
    extractor = QIExtractor(client)
    gate = ReleaseGate(extractor, normalizer, ResponseRewriter(client), GateConfig(**gate_config))
    session = RiskSession(population, TARGET_ID, "s1", RiskConfig(risk_threshold=3))
    return gate, session, extractor, normalizer


def raw_observations(extractor: QIExtractor, text: str):
    return extractor.extract(QUERY, text, source_turn=1).observations


def release(gate, session, extractor, condition, text, raw=_EXTRACT, **kwargs):
    if raw is _EXTRACT:
        raw = raw_observations(extractor, text)
    return gate.release(condition, session, query=QUERY, raw_response=text, raw_observations=raw, turn=1, **kwargs)


def commit_text(session, extractor, normalizer, text):
    session.commit(normalizer.normalize_all(raw_observations(extractor, text)))


# ── 하한 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "k_before, k_after, k_floor, ok",
    [(8, 5, 4, True), (8, 3, 4, False), (3, 3, 3, True), (3, 2, 3, False), (5, 0, 4, False), (0, 0, 0, True)],
)
def test_floor_satisfied(k_before, k_after, k_floor, ok):
    assert floor_satisfied(k_before, k_after, k_floor) is ok


# ── 실험 갈래 ─────────────────────────────────────────────

def test_arm_labels_round_trip():
    assert [Arm.parse(label).label for label in ("C0", "c1", "C1S")] == ["C0", "C1", "C1S"]
    assert Arm.parse("C1S").defender_mode is DefenderMode.STATELESS


@pytest.mark.parametrize("label", ["C0S", "C2", "C3"])
def test_unsupported_arms(label):
    with pytest.raises(ValueError):
        Arm.parse(label)


# ── C0 ────────────────────────────────────────────────────

def test_c0_releases_raw_candidate_unchanged():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C0, RAW_ZURICH)

    assert decision.path is ReleasePath.PASSTHROUGH
    assert decision.released_response == RAW_ZURICH
    assert [o.canonical_value for o in decision.released_observations] == ["Zurich, Switzerland"]
    assert (decision.k_before, decision.k_raw_projected, decision.k_released_projected) == (8, 3, 3)
    assert not decision.defender_enabled and not decision.intervened
    assert client.count("rewriter") == 0


# ── C1 ────────────────────────────────────────────────────

def test_c1_allows_value_above_suppress_threshold_when_no_generalization_exists():
    """스위스는 5명. 일반화 기준(5) 이하지만 국가 위 단계가 없고 억제 기준(3)은 넘으므로 그대로 공개된다."""
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_T1)

    assert decision.path is ReleasePath.ALLOWED
    assert decision.k_released_projected == 5
    assert (decision.defender_name, decision.defender_mode) == ("risk_defender", DefenderMode.STATEFUL)
    assert client.count("rewriter") == 0


def test_c1_generalizes_then_releases_rewritten_response():
    """30대는 5명(3 < k <= 5) → adult로 일반화하면 8명."""
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_AGE)

    assert decision.path is ReleasePath.REWRITTEN
    assert decision.released_response == "글의 내용으로 보아 이 사용자는 성인입니다."
    assert [o.canonical_value for o in decision.released_observations] == ["adult"]
    assert (decision.k_raw_projected, decision.k_floor, decision.k_released_projected) == (5, 4, 8)
    assert decision.generalized and not decision.suppressed and decision.floor_satisfied
    assert "바꿀 표현: 성인 (adult)" in client.prompts_of("rewriter")[0]   # Defender가 정한 값만 전달


def test_c1_suppresses_directly_at_or_below_suppress_threshold():
    """취리히는 3명. 수정본은 국가로 일반화해 보지 않고 바로 억제한다."""
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)

    assert decision.path is ReleasePath.REWRITTEN
    assert decision.released_observations == ()
    assert (decision.k_raw_projected, decision.k_released_projected) == (3, 8)
    assert decision.suppressed and not decision.generalized
    assert "삭제 | 속성: LOC" in client.prompts_of("rewriter")[0]


def test_c1_suppresses_only_the_risky_qi_in_a_response():
    """State: 스위스(5명). 30대(4명)는 통과, 직업까지 더하면 2명이라 직업만 억제."""
    client = RoleClient([])
    gate, session, extractor, normalizer = build(client)
    commit_text(session, extractor, normalizer, RAW_T1)

    decision = release(gate, session, extractor, Condition.C1, RAW_T2)

    assert decision.path is ReleasePath.REWRITTEN
    assert [o.attribute for o in decision.released_observations] == ["AGE"]
    assert (decision.k_before, decision.k_raw_projected, decision.k_released_projected) == (5, 2, 4)
    assert "삭제 | 속성: OCCP" in client.prompts_of("rewriter")[0]


def test_gate_never_changes_state():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    release(gate, session, extractor, Condition.C1, RAW_AGE)
    assert session.k_current == 8 and session.committed_qis == {}


def test_second_round_when_first_rewrite_still_leaks():
    """첫 고쳐 쓰기가 도시를 그대로 남기면 다시 판정·고쳐 쓰기를 한다."""
    outputs = iter([RAW_ZURICH, "거주 지역은 기록에서 확인하기 어렵습니다."])
    client = RoleClient([], rewrites=lambda original: next(outputs))
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)

    assert decision.path is ReleasePath.REWRITTEN
    assert [r.floor_satisfied for r in decision.rounds] == [False, True]
    assert decision.k_released_projected == 8


def test_rounds_exhausted_falls_back_to_refusal():
    client = RoleClient([], rewrites=lambda original: RAW_ZURICH)      # 몇 번을 고쳐도 도시가 남음
    gate, session, extractor, _ = build(client, max_defense_rounds=2, refusal_text="답변 불가")
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)

    assert decision.path is ReleasePath.REFUSAL
    assert decision.refusal_reason is RefusalReason.ROUNDS_EXHAUSTED
    assert decision.released_response == "답변 불가"
    assert decision.released_observations == ()
    assert decision.k_released_projected == decision.k_before == 8
    assert client.count("rewriter") == 2


def test_unusable_rewrite_falls_back_to_refusal():
    client = RoleClient([], rewrites=lambda original: "   ")
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)
    assert decision.refusal_reason is RefusalReason.REWRITE_FAILED


def test_reextraction_failure_falls_back_to_refusal():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    raw = raw_observations(extractor, RAW_ZURICH)
    client._extractor = lambda text: "JSON이 아님"                  # 이후 추출기 출력이 깨짐
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH, raw=raw)
    assert decision.refusal_reason is RefusalReason.REEXTRACTION_FAILED


# ── 일반화 표현을 규칙으로 읽지 못했을 때 ─────────────────

def test_unreadable_generalized_phrase_is_recorded_as_instructed_value():
    """Rewrite LLM이 '성인' 대신 풀어 쓴 표현을 내면 정규화 규칙이 못 읽는다. Defender가 지시한 값(adult)으로 기록한다."""
    paraphrase = "글의 내용으로 보아 이 사용자는 어른입니다."
    extractor_json = ('{"observations": [{"attribute": "AGE", "raw_value": "어른", "value_en": "grown-up", '
                      '"inference_mode": "EXPLICIT", "confidence": 5, "evidence_spans": ["어른입니다"]}]}')
    client = RoleClient([], rewrites=lambda original: paraphrase)
    gate, session, extractor, _ = build(client)
    raw = raw_observations(extractor, RAW_AGE)
    client._extractor = lambda text: extractor_json

    decision = release(gate, session, extractor, Condition.C1, RAW_AGE, raw=raw)

    released = decision.released_observations[0]
    assert (released.canonical_value, released.reason) == ("adult", MappingReason.DEFENDER_INSTRUCTED)
    assert decision.rounds[0].adopted_ids == (released.observation_id,)
    assert decision.k_released_projected == 8


def test_generalized_phrase_missed_by_the_extractor_is_still_recorded():
    """고쳐 쓴 응답에 '성인'이 글자 그대로 있는데 추출기가 놓치면, 관찰을 만들어 State에 반영되게 한다."""
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    raw = raw_observations(extractor, RAW_AGE)
    client._extractor = lambda text: '{"observations": []}'          # 재추출에서 아무것도 못 찾음

    decision = release(gate, session, extractor, Condition.C1, RAW_AGE, raw=raw)

    assert decision.released_response == "글의 내용으로 보아 이 사용자는 성인입니다."
    released = decision.released_observations[0]
    assert (released.observation_id, released.canonical_value, released.raw_value) == ("t1-adopt-AGE", "adult", "성인")
    assert released.reason is MappingReason.DEFENDER_INSTRUCTED


def test_nothing_is_recorded_when_the_generalized_phrase_is_absent():
    client = RoleClient([], rewrites=lambda original: "나이는 기록에서 확인하기 어렵습니다.")
    gate, session, extractor, _ = build(client)
    decision = release(gate, session, extractor, Condition.C1, RAW_AGE)
    assert decision.path is ReleasePath.REWRITTEN and decision.released_observations == ()


def test_instructed_value_adoption_can_be_turned_off():
    client = RoleClient([], rewrites=lambda original: "글의 내용으로 보아 이 사용자는 어른입니다.")
    gate, session, extractor, _ = build(client, adopt_instructed_value=False)
    raw = raw_observations(extractor, RAW_AGE)
    client._extractor = lambda text: ('{"observations": [{"attribute": "AGE", "raw_value": "어른", "value_en": null, '
                                      '"inference_mode": "EXPLICIT", "confidence": 5, "evidence_spans": ["어른입니다"]}]}')

    decision = release(gate, session, extractor, Condition.C1, RAW_AGE, raw=raw)
    assert not decision.released_observations[0].is_mapped
    assert decision.rounds[0].adopted_ids == ()


# ── raw candidate 추출 실패 ───────────────────────────────

def test_raw_extraction_failure_is_handled_per_condition():
    client = RoleClient([])
    gate, session, extractor, _ = build(client)

    c0 = release(gate, session, extractor, Condition.C0, RAW_ZURICH, raw=None)
    c1 = release(gate, session, extractor, Condition.C1, RAW_ZURICH, raw=None)

    assert (c0.path, c0.released_response, c0.released_observations) == (ReleasePath.PASSTHROUGH, RAW_ZURICH, ())
    assert (c1.path, c1.refusal_reason) == (ReleasePath.REFUSAL, RefusalReason.RAW_EXTRACTION_FAILED)


# ── 이미 기준 아래인 State ────────────────────────────────

def test_preexisting_risk_keeps_floor_at_current_k():
    """k가 이미 3(<k_req 4)이면 하한은 3이다. 응답은 나가되 k가 3 아래로 내려가지 않는다."""
    client = RoleClient([])
    gate, session, extractor, normalizer = build(client)
    commit_text(session, extractor, normalizer, RAW_ZURICH)          # 취리히 → k=3
    assert session.k_current == 3

    decision = release(gate, session, extractor, Condition.C1, RAW_T2)

    assert decision.k_floor == 3
    assert decision.path is ReleasePath.REWRITTEN
    assert decision.k_released_projected == 3 and decision.floor_satisfied


# ── 무상태 Defender ───────────────────────────────────────

def test_stateless_defender_judges_each_turn_from_empty_state():
    """
    State: 스위스(5명). 응답: 30대, 소프트웨어 엔지니어.
      누적 판정   : 스위스+30대 = 4명 통과, +직업 = 2명이라 직업 억제 → 4명
      무상태 판정 : 빈 State에서 30대 = 5명 → adult(8명)로 일반화, adult+직업 = 4명이라 직업 통과
                    실제 State에 더하면 스위스+adult+직업 = 3명 → 요구 익명성(4) 미달
    """
    client = RoleClient([])
    gate, session, extractor, normalizer = build(client)
    commit_text(session, extractor, normalizer, RAW_T1)

    decision = release(gate, session, extractor, Condition.C1, RAW_T2, defender_mode=DefenderMode.STATELESS)

    assert decision.defender_mode is DefenderMode.STATELESS
    assert decision.released_response == "글의 내용으로 보아 성인이고 소프트웨어 엔지니어로 일합니다."
    assert (decision.judged_k_floor, decision.k_floor) == (4, 4)
    assert decision.rounds[0].k_projected == 4          # Defender가 본 세계에서는 안전
    assert decision.k_released_projected == 3           # 실제로는 기준 아래
    assert decision.floor_satisfied is False


# ── 지원하지 않는 조건 ────────────────────────────────────

@pytest.mark.parametrize("condition", [Condition.C2, Condition.C3])
def test_unsupported_conditions(condition):
    client = RoleClient([])
    gate, session, extractor, _ = build(client)
    with pytest.raises(ValueError):
        release(gate, session, extractor, condition, RAW_ZURICH)


# ── 고쳐 쓴 응답에 남은 근거 문구 ─────────────────────────

def leaky_setup(rewritten_texts, **gate_config):
    """
    재작성기가 차례로 rewritten_texts를 내놓고, 재추출은 아무것도 뽑지 못하는 상황
    (추출기가 남은 단서를 놓치는 경우). 원 응답 추출만 정상이다.
    """
    from tests.orchestrator.sample_data import extract_from

    outputs = iter(rewritten_texts)
    calls = {"n": 0}

    def extractor(text):
        calls["n"] += 1
        return extract_from(text) if calls["n"] == 1 else '{"observations": []}'

    client = RoleClient([], rewrites=lambda original: next(outputs), extractor=extractor)
    return (client, *build(client, **gate_config))


CLEAN = "거주 지역은 기록에서 확인하기 어렵습니다."


def test_leftover_evidence_fails_the_round_even_if_reextraction_misses_it():
    """1회차 재작성이 '취리히에 거주' 문구를 그대로 두었다. 재추출은 놓쳤지만 통과시키지 않고 다시 고치게 한다."""
    client, gate, session, extractor, _ = leaky_setup([RAW_ZURICH, CLEAN])
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)

    first, second = decision.rounds
    assert first.residual_evidence == ("취리히에 거주",) and not first.floor_satisfied
    assert second.residual_evidence == () and second.floor_satisfied
    assert decision.path is ReleasePath.REWRITTEN and decision.released_response == CLEAN
    assert "취리히에 거주" in client.prompts_of("rewriter")[1]          # 남은 문구를 다시 지시한다
    assert first.to_log_dict()["residual_evidence"] == ["취리히에 거주"]


def test_evidence_left_in_every_round_ends_in_refusal():
    _, gate, session, extractor, _ = leaky_setup([RAW_ZURICH, RAW_ZURICH])
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)
    assert decision.path is ReleasePath.REFUSAL
    assert decision.refusal_reason is RefusalReason.ROUNDS_EXHAUSTED


def test_residual_check_can_be_turned_off():
    """끄면 재추출 결과만 믿는다 (Part 10까지의 동작). 문구가 남은 응답이 그대로 공개된다."""
    _, gate, session, extractor, _ = leaky_setup([RAW_ZURICH], check_residual_evidence=False)
    decision = release(gate, session, extractor, Condition.C1, RAW_ZURICH)
    assert decision.path is ReleasePath.REWRITTEN and decision.released_response == RAW_ZURICH
    assert len(decision.rounds) == 1


def test_short_evidence_words_are_not_checked():
    from orchestrator.release_gate import residual_instructions
    from orchestrator.rewriter import RewriteAction, RewriteInstruction

    item = RewriteInstruction("t1-o1", "SEX", RewriteAction.SUPPRESS, "his", ("his", "never judge a gamer by his day job"), None)
    (left,) = residual_instructions([item], "this is fine. never judge a gamer by his day job!")
    assert left.evidence == ("never judge a gamer by his day job",)
    assert residual_instructions([item], "this is his") == []
