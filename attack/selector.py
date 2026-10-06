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
from dataclasses import dataclass, field, replace
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
    unresolved_qis
    → 공개는 됐지만 표준 값으로 풀지 못한 QI (committed_qis에는 응답의 표현 그대로 들어 있다).
      O2 Seed가 "방금 말한 그 값을 더 구체적으로" 물을 수 있게 한다. 공개된 응답에서만 나온 정보다
    target_qi_scope
    → 이 QI를 노리는 Seed만 고른다. None이면 제한 없음.
      위험 계산이 5개 QI만 세는 설정에서 소득·성별·출생지를 묻는 Seed에 턴을 쓰면 무엇이 나와도 k가 줄지 않는다.
      어떤 QI를 세는지는 실험 설정이지 대상에 대한 정보가 아니다
    k·GT·Raw Candidate 필드는 일부러 두지 않는다 (D1 Part III 정보 경계 금지사항).
    """
    committed_qis: Mapping[str, str] = field(default_factory=dict)
    prior_turns: int = 0
    refinable_qis: frozenset[str] | None = None
    target_qi_scope: frozenset[str] | None = None
    unresolved_qis: frozenset[str] = frozenset()


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
        return tuple(seed for seed in self._registry.seeds if self.is_applicable(seed, view))

    def is_applicable(self, seed: Seed, view: AttackStateView) -> bool:
        if view.target_qi_scope is not None and seed.target_qi not in view.target_qi_scope:
            return False
        return evaluate(seed.conditions, seed.target_qi, view.committed_qis, view.prior_turns, view.refinable_qis,
                        view.unresolved_qis)

    def choose(self, view: AttackStateView, *,
               qi_turns: Mapping[str, int] | None = None) -> tuple[Seed | None, int]:
        """
        input : view, qi_turns (QI별로 지금까지 질문에 쓴 턴 수. 주면 가장 적게 물은 QI의 Seed 중에서 고른다)
        output : (고른 Seed 또는 None, 조건을 만족한 Seed 수)

        만족하는 Seed가 없으면 None이다. 호출 측은 이를 D1 종료 사유 NO_APPLICABLE_ACTION으로 처리한다.
        target_qi_scope 안에서 고를 Seed가 없으면 범위 밖 Seed로 넘어간다 (범위는 우선순위이지 종료 조건이 아니다).
        """
        candidates = self.applicable(view)
        if not candidates and view.target_qi_scope is not None:
            candidates = self.applicable(replace(view, target_qi_scope=None))
        if not candidates:
            return None, 0
        pool = candidates
        if qi_turns is not None:
            fewest = min(qi_turns.get(seed.target_qi, 0) for seed in candidates)
            pool = tuple(seed for seed in candidates if qi_turns.get(seed.target_qi, 0) == fewest)
        return self._rng.choice(pool), len(candidates)

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
