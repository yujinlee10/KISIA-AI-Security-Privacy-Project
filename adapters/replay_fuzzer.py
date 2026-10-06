# 정해진 질문을 순서대로 내놓는 Fuzzer 대역
#
# 쓰는 곳
#   orchestrator/replay.py     : 보관된 위험 경로의 질문을 새 세션에서 그대로 재실행 (Exact Replay, D1 Stage 13)
#   orchestrator/experiment.py : 갈래 간 같은 질문 사용 (첫 갈래가 만든 질문을 나머지 갈래에 그대로 던진다)
#
# Seed를 고르지도, LLM을 부르지도, k 변화에 반응하지도 않는다.

from __future__ import annotations

from typing import Any, Sequence

from adapters.fuzzer_adapter import AttackStep, NoApplicableSeedError
from attack.selector import AttackStateView
from fuzzer.feedback import FuzzerFeedback, evaluate_feedback


def queries_of(attack_steps: Sequence[AttackStep]) -> list[dict[str, Any]]:
    """세션이 던진 질문들을 ReplayFuzzer가 받는 모양으로 바꾼다 (보관소의 queries와 같은 모양)."""
    return [
        {"turn": step.turn, "query": step.query, "seed_id": step.seed_id, "seed_version": step.seed_version,
         "objective": step.objective, "scenario_code": step.scenario_code, "target_qi": step.target_qi,
         "strategy": step.strategy, "reset_reason": step.reset_reason}
        for step in attack_steps
    ]


class ReplayFuzzer:
    """
    보관된 질문을 순서대로 내놓는다. FuzzerAdapter와 같은 방법(next_query·observe)을 갖지만
    Seed를 고르지도, LLM을 부르지도, k 변화에 반응하지도 않는다.
    """

    def __init__(self, queries: Sequence[dict[str, Any]], risk_threshold: int, *, label: str = "REPLAY") -> None:
        """label → 로그의 reset_reason에 남길 이름 (보관 경로 재실행은 REPLAY, 갈래 간 같은 질문 사용은 PAIRED)"""
        self._queries = list(queries)
        self._risk_threshold = risk_threshold
        self._label = label
        self._position = 0

    def next_query(self, view: AttackStateView) -> AttackStep:
        if self._position >= len(self._queries):
            raise NoApplicableSeedError("보관된 질문을 모두 재실행했습니다.")
        item = self._queries[self._position]
        self._position += 1
        return AttackStep(
            turn=self._position,
            query=item["query"],
            seed_id=item["seed_id"],
            seed_version=item.get("seed_version", ""),
            objective=item["objective"],
            scenario_code=item["scenario_code"],
            target_qi=item["target_qi"],
            reset_reason=self._label,
            strategy=None,
            feedback_status=None,
            bound_seed_text="",
            mutation_instruction=None,
            mutation_attempts=0,
            mutation_valid=True,
            invalid_reasons=(),
            fallback_used=False,
            applicable_seed_count=0,
            mutation_input_tokens=0,
            mutation_output_tokens=0,
            mutation_latency_ms=0.0,
        )

    def observe(self, k_before: int, k_after: int) -> FuzzerFeedback:
        """기록용으로 판정만 계산한다 (다음 질문에는 쓰이지 않는다)."""
        risk = (1.0 / k_after if k_after > 0 else 0.0) - (1.0 / k_before if k_before > 0 else 0.0)
        return evaluate_feedback(k_before=k_before, k_after=k_after, risk_gain=risk, threshold=self._risk_threshold)
