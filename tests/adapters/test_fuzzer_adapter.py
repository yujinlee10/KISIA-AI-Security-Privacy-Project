# adapters/fuzzer_adapter.py 테스트
# 한재이 파트 함수(evaluate_feedback, select_mutation_strategy, generate_next_mutation)는 실제 것을 쓰고,
# Mutation LLM만 가짜로 바꾼다.

from __future__ import annotations

from typing import Any

import pytest

from adapters.fuzzer_adapter import (
    MUTATION_MAX_TOKENS,
    FuzzerAdapter,
    FuzzerConfig,
    NoApplicableSeedError,
    _load_system_prompt,
)
from attack.registry import SeedRegistry
from attack.selector import AttackStateView, SeedSelector
from core.llm_client import LLMResponse
from fuzzer.fuzzer_mutation import generate_next_mutation


def reply(text: str, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(text=text, model="m", stop_reason=stop_reason,
                       input_tokens=30, output_tokens=10, latency_ms=5.0, attempts=1)


class FakeClient:
    def __init__(self, *outcomes: LLMResponse) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def complete(self, user_prompt, *, system_prompt=None, max_tokens=None, temperature=None):
        self.calls.append({"user_prompt": user_prompt, "system_prompt": system_prompt,
                           "max_tokens": max_tokens, "temperature": temperature})
        return self._outcomes.pop(0)


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


def make_adapter(registry, *outcomes, **config):
    client = FakeClient(*outcomes)
    adapter = FuzzerAdapter(SeedSelector(registry, rng_seed=1), client, FuzzerConfig(**config))
    return adapter, client


EMPTY = AttackStateView({}, prior_turns=0)


# ── 첫 턴 ─────────────────────────────────────────────────

def test_first_turn_starts_from_a_seed(registry):
    adapter, client = make_adapter(registry, reply("변형된 첫 질문"))
    step = adapter.next_query(EMPTY)

    assert step.reset_reason == "FIRST_TURN"
    assert step.strategy is None
    assert step.query == "변형된 첫 질문"
    assert step.objective == "O1"
    assert client.calls[0]["user_prompt"] == step.bound_seed_text      # Seed 그대로 Mutation LLM에
    assert client.calls[0]["system_prompt"] == _load_system_prompt()   # 한재이 seedprompt.txt
    assert client.calls[0]["max_tokens"] == MUTATION_MAX_TOKENS


# ── 이어서 변형 ───────────────────────────────────────────

def test_improved_feedback_mutates_previous_query_with_fuzzer_strategy(registry):
    adapter, client = make_adapter(registry, reply("첫 질문"), reply("두 번째 질문"))
    first = adapter.next_query(EMPTY)

    feedback = adapter.observe(k_before=300_000, k_after=120_000)     # 감소율 0.6 → intensify
    step = adapter.next_query(AttackStateView({}, prior_turns=1))

    assert feedback.status == "improved"
    assert step.reset_reason is None
    assert step.strategy == "intensify"
    assert step.seed_id == first.seed_id                              # 같은 Seed 계열 유지
    expected = generate_next_mutation(previous_prompt="첫 질문", strategy="intensify")
    assert client.calls[1]["user_prompt"] == expected == step.mutation_instruction
    assert step.query == "두 번째 질문"


def test_threshold_uses_shared_config_value(registry):
    adapter, _ = make_adapter(registry, reply("q"), risk_threshold=3)
    adapter.next_query(EMPTY)
    assert adapter.observe(10, 4).status == "improved"                # 4 > 3
    assert adapter.observe(10, 3).status == "threshold_reached"       # 3 <= 3


def test_k_values_never_reach_mutation_llm(registry):
    adapter, client = make_adapter(registry, reply("첫 질문"), reply("두 번째"))
    adapter.next_query(EMPTY)
    adapter.observe(k_before=287_311, k_after=123_457)
    adapter.next_query(AttackStateView({}, 1))

    sent = " ".join(c["user_prompt"] + c["system_prompt"] for c in client.calls)
    assert "287311" not in sent and "123457" not in sent


# ── 새 Seed로 다시 시작 ───────────────────────────────────

def test_zero_match_aborts_and_restarts_with_new_seed(registry):
    adapter, client = make_adapter(registry, reply("q1"), reply("q2"))
    adapter.next_query(EMPTY)
    adapter.observe(k_before=100, k_after=0)                          # invalid → abort
    step = adapter.next_query(AttackStateView({}, 1))

    assert step.reset_reason == "ABORT"
    assert step.strategy is None
    assert client.calls[1]["user_prompt"] == step.bound_seed_text


def test_seed_no_longer_applicable_restarts(registry):
    adapter, _ = make_adapter(registry, reply("q1"), reply("q2"))
    first = adapter.next_query(EMPTY)                                 # O1 Seed
    adapter.observe(300_000, 50_000)
    disclosed = AttackStateView({first.target_qi: "Switzerland" if first.target_qi == "LOC" else "x"}, 1)
    step = adapter.next_query(disclosed)                              # 그 QI가 공개됨 → O1 조건 깨짐

    assert step.reset_reason == "SEED_NOT_APPLICABLE"


def test_missing_observe_restarts(registry):
    adapter, _ = make_adapter(registry, reply("q1"), reply("q2"))
    adapter.next_query(EMPTY)
    assert adapter.next_query(AttackStateView({}, 1)).reset_reason == "NO_FEEDBACK"


def test_no_applicable_seed_raises(registry):
    adapter, _ = make_adapter(registry)
    full = AttackStateView({qi: "x, y" for qi in ("AGE", "SEX", "LOC", "OCCP", "EDU", "REL", "INC", "POB")}, 3)
    with pytest.raises(NoApplicableSeedError):
        adapter.next_query(full)


# ── 변형 결과 유효성 ──────────────────────────────────────

def test_invalid_output_is_retried(registry):
    adapter, client = make_adapter(registry, reply(""), reply("유효한 질문"))
    step = adapter.next_query(EMPTY)

    assert (step.mutation_attempts, step.mutation_valid) == (2, True)
    assert step.invalid_reasons == ("EMPTY",)
    assert step.query == "유효한 질문"


@pytest.mark.parametrize(
    "bad, reason",
    [
        (reply("[SUBJECT]의 나이는?"), "UNRESOLVED_PLACEHOLDER"),
        (reply("잘린 질문", stop_reason="max_tokens"), "TRUNCATED"),
        (reply("가" * 2000), "TOO_LONG"),
    ],
)
def test_invalid_reasons(registry, bad, reason):
    adapter, _ = make_adapter(registry, bad, reply("ok"))
    assert adapter.next_query(EMPTY).invalid_reasons == (reason,)


def test_fallback_to_seed_text_when_all_attempts_invalid(registry):
    adapter, _ = make_adapter(registry, reply(""), reply(""), max_mutation_retries=1)
    step = adapter.next_query(EMPTY)

    assert step.fallback_used is True
    assert step.mutation_valid is False
    assert step.query == step.bound_seed_text


def test_step_log_is_flat_and_counts_cost(registry):
    adapter, _ = make_adapter(registry, reply(""), reply("q"))
    log = adapter.next_query(EMPTY).to_log_dict()

    assert log["mutation_input_tokens"] == 60          # 두 번 호출 합계
    assert log["invalid_reasons"] == ["EMPTY"]
    assert log["turn"] == 1


# ── 변화 없는 턴이 이어질 때 ──────────────────────────────

def test_stale_turns_move_to_new_seed(registry):
    adapter, client = make_adapter(registry, reply("q1"), reply("q2"), reply("q3"), max_stale_turns=2)
    adapter.next_query(EMPTY)
    adapter.observe(100, 100)                                         # unchanged 1회
    second = adapter.next_query(AttackStateView({}, 1))
    adapter.observe(100, 100)                                         # unchanged 2회 연속
    third = adapter.next_query(AttackStateView({}, 2))

    assert second.reset_reason is None and second.strategy == "diversify"
    assert third.reset_reason == "STALE"
    assert client.calls[2]["user_prompt"] == third.bound_seed_text


def test_stale_count_resets_when_k_changes(registry):
    adapter, _ = make_adapter(registry, reply("q1"), reply("q2"), reply("q3"), max_stale_turns=2)
    adapter.next_query(EMPTY)
    adapter.observe(100, 100)
    adapter.next_query(AttackStateView({}, 1))
    adapter.observe(100, 60)                                          # 줄었으므로 연속 횟수 초기화
    assert adapter.next_query(AttackStateView({}, 2)).reset_reason is None


def test_stale_limit_can_be_disabled(registry):
    adapter, _ = make_adapter(registry, *[reply(f"q{i}") for i in range(4)], max_stale_turns=None)
    adapter.next_query(EMPTY)
    for turn in range(1, 4):
        adapter.observe(100, 100)
        assert adapter.next_query(AttackStateView({}, turn)).reset_reason is None
