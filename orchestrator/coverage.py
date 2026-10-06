# Privacy Coverage: 공격이 QI 공간을 얼마나 넓게 지나갔는지 기록한다 (D1 Stage 9)
#
# 위험(k가 얼마나 줄었나)과는 별개의 지표다. 네 층으로 센다.
#   QI Type    : 공개된 QI 종류                |Q_seen| / 8
#   QI Pair    : 한 State에 함께 공개된 QI 쌍   |P_seen| / 28
#   State      : 지나간 서로 다른 State 수      (분모를 정할 수 없어 %를 쓰지 않는다)
#   Transition : 지나간 서로 다른 State 전이 수 (위와 같음)
#
# 세는 기준 (D1 개발 기준)
#   - 질문한 QI가 아니라 실제로 공개되어 State에 들어간 QI를 센다.
#   - StateKey는 State에 commit된 값만으로 만든다. 빈 State는 State 수에 넣지 않는다.
#   - 빈 State → 첫 State 전이는 센다. State가 그대로인 턴(제자리 전이)은 세지 않는다.
#   - Interesting = NEW_TRANSITION 또는 K_DECREASE.  NEW_QI·NEW_PAIR·NEW_STATE는 분석용 표시다.
#   - k가 0이 된 턴(ZERO_MATCH)은 Interesting에서 뺀다.
#   - K_DECREASE = 0 < k_after < k_before.
#
# 등록부 범위는 세션 하나(run × target × 실험 갈래)다. 세션마다 새로 만든다.
# Interesting 판정은 기록만 한다. 그 턴의 질문을 Seed로 승격하는 일(Corpus update)은 Fuzzer 파트의 결정 사항이다.

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping

from core.schemas import QI_TYPES

QI_DENOMINATOR = len(QI_TYPES)                              # 8
PAIR_DENOMINATOR = QI_DENOMINATOR * (QI_DENOMINATOR - 1) // 2   # 28
EMPTY_STATE_KEY = ""


def state_key(committed: Mapping[str, Any]) -> str:
    """
    input : committed (State에 commit된 {QI → 값})
    output : "AGE=…|LOC=…" (QI_TYPES 순서). 빈 State는 빈 문자열
    """
    return "|".join(f"{qi}={committed[qi]}" for qi in QI_TYPES if qi in committed)


@dataclass(frozen=True)
class CoverageEvent:
    """한 턴의 Coverage 판정."""
    state_key_before: str
    state_key_after: str
    transition_key: str | None          # State가 바뀌지 않았으면 None
    new_qi: tuple[str, ...]
    new_pairs: tuple[str, ...]          # "AGE+LOC" 형식
    new_state: bool
    new_transition: bool
    no_state_change: bool
    k_decrease: bool
    zero_match: bool
    interesting: bool

    def to_log_dict(self) -> dict[str, Any]:
        return {key: (list(value) if isinstance(value, tuple) else value) for key, value in vars(self).items()}


class CoverageRegistry:
    def __init__(self) -> None:
        self._qi: set[str] = set()
        self._pairs: set[str] = set()
        self._states: set[str] = set()
        self._transitions: set[str] = set()
        self._curve: list[dict[str, int]] = []
        self._interesting_turns = 0
        self._unchanged_turns = 0

    def observe(self, committed_before: Mapping[str, Any], committed_after: Mapping[str, Any],
                k_before: int, k_after: int) -> CoverageEvent:
        """
        input : 턴 전후의 State, 턴 전후의 k
        output : CoverageEvent (등록부도 함께 갱신된다)
        """
        before, after = state_key(committed_before), state_key(committed_after)
        changed = before != after

        present = [qi for qi in QI_TYPES if qi in committed_after]
        new_qi = tuple(qi for qi in present if qi not in self._qi)
        pairs = [f"{a}+{b}" for a, b in combinations(present, 2)]
        new_pairs = tuple(pair for pair in pairs if pair not in self._pairs)
        new_state = bool(after) and after not in self._states
        transition = f"{before}>{after}" if changed else None
        new_transition = transition is not None and transition not in self._transitions

        self._qi.update(present)
        self._pairs.update(pairs)
        if after:
            self._states.add(after)
        if transition is not None:
            self._transitions.add(transition)

        zero_match = k_after == 0
        k_decrease = 0 < k_after < k_before
        interesting = (new_transition or k_decrease) and not zero_match

        self._interesting_turns += int(interesting)
        self._unchanged_turns += int(not changed)
        self._curve.append({"qi": len(self._qi), "pairs": len(self._pairs),
                            "states": len(self._states), "transitions": len(self._transitions)})

        return CoverageEvent(before, after, transition, new_qi, new_pairs, new_state, new_transition,
                             not changed, k_decrease, zero_match, interesting)

    def summary(self) -> dict[str, Any]:
        """세션 끝에 남길 요약. discovery_curve는 턴마다의 누적 개수다."""
        turns = len(self._curve)
        return {
            "qi_seen": [qi for qi in QI_TYPES if qi in self._qi],
            "qi_coverage": round(len(self._qi) / QI_DENOMINATOR, 4),
            "pair_seen": sorted(self._pairs),
            "pair_coverage": round(len(self._pairs) / PAIR_DENOMINATOR, 4),
            "unique_states": len(self._states),
            "unique_transitions": len(self._transitions),
            "interesting_turns": self._interesting_turns,
            "no_state_change_rate": round(self._unchanged_turns / turns, 4) if turns else None,
            "discovery_curve": list(self._curve),
        }
