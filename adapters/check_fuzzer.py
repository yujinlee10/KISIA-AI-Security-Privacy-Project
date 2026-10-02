# Fuzzer 경로 확인: python -m adapters.check_fuzzer
#
# Target 없이 공격 질문 생성만 두 턴 돌려 본다 (Mutation LLM 호출 2회).
#   턴 1 : 빈 State → 조건에 맞는 Seed 선택 → 한재이 seedprompt로 1차 변형
#   턴 2 : k가 300,000 → 120,000으로 줄었다고 가정 → 한재이 feedback이 전략 선택 → 직전 질문 변형
# k 값은 확인용 가정값이다. 실제 k는 Part 6 orchestrator에서 risk 어댑터가 넘긴다.

from __future__ import annotations

import argparse

from adapters.fuzzer_adapter import FuzzerAdapter, FuzzerConfig
from attack.registry import SeedRegistry
from attack.selector import AttackStateView, SeedSelector
from core.llm_client import build_client


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 선택 → Mutation 두 턴 확인")
    parser.add_argument("--rng-seed", type=int, default=42)
    args = parser.parse_args()

    registry = SeedRegistry.load()
    print(f"------ Seed Registry {len(registry)}개 {registry.count_by('objective')} "
          f"({registry.mode.value}) ------")

    with build_client() as client:
        adapter = FuzzerAdapter(SeedSelector(registry, rng_seed=args.rng_seed), client, FuzzerConfig())

        first = adapter.next_query(AttackStateView({}, prior_turns=0))
        _print_step(first)

        feedback = adapter.observe(k_before=300_000, k_after=120_000)
        print(f"------ (가정) k 300,000 → 120,000 : status={feedback.status} ------")

        second = adapter.next_query(AttackStateView({}, prior_turns=1))
        _print_step(second)


def _print_step(step) -> None:
    print(f"------ 턴 {step.turn}: {step.seed_id} ({step.objective}/{step.scenario_code}/{step.target_qi}) "
          f"시작사유={step.reset_reason} 전략={step.strategy} 시도={step.mutation_attempts} "
          f"유효={step.mutation_valid} 대체={step.fallback_used} ------")
    print("Seed  :", step.bound_seed_text)
    print("질문  :", step.query)


if __name__ == "__main__":
    main()
