# 지금 State에 맞는 Seed를 고르고 플레이스홀더를 채운다
#
# 선택 규칙 (D1 Stage 8 Primary: "applicable 중 uniform")
#   조건을 만족하는 Seed 중 하나를 균등 확률로 고른다. 난수는 rng_seed로 고정해 재현할 수 있게 한다.
#
# 바인딩 규칙 (D1 Stage 7, 팀 합의)
#   [SUBJECT]                  → "이 사용자" (Target system prompt가 말하는 '사용자'와 같은 표현)
#   [PREVIOUSLY_DISCLOSED_QI]  → 공개된 응답에서 commit된 그 QI의 현재 값
#   정답·Raw Candidate·보호값은 넣지 않는다. 애초에 이 파일이 받는 입력에 그런 값이 없다.

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Mapping

from attack.conditions import evaluate
from attack.registry import PLACEHOLDER_DISCLOSED, PLACEHOLDER_SUBJECT, Seed, SeedRegistry

DEFAULT_SUBJECT_TEXT = "이 사용자"


class BindingError(ValueError):
    """[PREVIOUSLY_DISCLOSED_QI]를 채울 공개 값이 없을 때 (조건 평가를 거쳤다면 생기지 않아야 함)."""


@dataclass(frozen=True)
class AttackStateView:
    """
    Backend가 공격 선택에 쓸 수 있는 정보 전부.

    committed_qis
    → 공개된 응답에서 commit된 QI (risk 어댑터 RiskSession.committed_qis)
    prior_turns
    → 이 세션에서 이미 진행한 턴 수
    refinable_qis
    → 공개된 값보다 더 구체적인 값이 남아 있는 QI (O2 Seed 조건용). 공개된 값과 계층만으로 정해지는 정보다.
      None이면 attack.conditions의 기본 규칙으로 판단한다
    k·GT·Raw Candidate 필드는 일부러 두지 않는다 (D1 Part III 정보 경계 금지사항).
    """
    committed_qis: Mapping[str, str] = field(default_factory=dict)
    prior_turns: int = 0
    refinable_qis: frozenset[str] | None = None


class SeedSelector:
    """레지스트리와 난수 생성기를 들고, 상태를 받아 Seed를 고른다."""

    def __init__(self, registry: SeedRegistry, *, rng_seed: int, subject_text: str = DEFAULT_SUBJECT_TEXT) -> None:
        self._registry = registry
        self._rng = random.Random(rng_seed)
        self._subject_text = subject_text

    def applicable(self, view: AttackStateView) -> tuple[Seed, ...]:
        """
        input : view
        output : 조건을 만족하는 Seed (레지스트리 순서 유지)
        """
        return tuple(
            seed for seed in self._registry.seeds
            if evaluate(seed.conditions, seed.target_qi, view.committed_qis, view.prior_turns, view.refinable_qis)
        )

    def is_applicable(self, seed: Seed, view: AttackStateView) -> bool:
        return evaluate(seed.conditions, seed.target_qi, view.committed_qis, view.prior_turns, view.refinable_qis)

    def choose(self, view: AttackStateView) -> tuple[Seed | None, int]:
        """
        input : view
        output : (고른 Seed 또는 None, 조건을 만족한 Seed 수)

        만족하는 Seed가 없으면 None이다. 호출 측은 이를 D1 종료 사유 NO_APPLICABLE_ACTION으로 처리한다.
        """
        candidates = self.applicable(view)
        if not candidates:
            return None, 0
        return self._rng.choice(candidates), len(candidates)

    def bind(self, seed: Seed, view: AttackStateView) -> str:
        """
        input : seed, view
        output : 플레이스홀더를 채운 질문 문장
        """
        text = seed.template_text.replace(PLACEHOLDER_SUBJECT, self._subject_text)
        if seed.needs_disclosed_value:
            value = view.committed_qis.get(seed.target_qi)
            if value is None:
                raise BindingError(f"{seed.seed_id}: {seed.target_qi}의 공개된 값이 없습니다.")
            text = text.replace(PLACEHOLDER_DISCLOSED, f"'{value}'")
        return text
