# 공개 전 일반화 탐색 (D1 Stage 11 개발 기준)
#
# 입력 : 지금 State, 이번 응답이 새로 드러내는 QI들(ΔQ), 지켜야 할 하한 k_floor
# 출력 : QI마다 "그대로 / 몇 단계 위 node로 / 억제"를 정한 계획
#
# 규칙
#   1) 새 공개분만 다룬다. 이미 공개된 값과 같거나 더 넓은 값은 새 정보가 아니다 (호출 측이 걸러서 넘긴다).
#   2) 그대로 공개했을 때 k >= k_floor 이고 0명이 되지 않으면 손대지 않는다.
#   3) 아니면 "한 번에 QI 하나를 부모로 한 단계" 올린 조합부터 차례로 넓혀 가며 찾는다 (너비 우선).
#      올린 단계 수의 합이 같은 조합들을 한 층으로 보고, 안전한 조합이 처음 나온 층에서 멈춘다.
#   4) 같은 층에 안전한 조합이 여럿이면 정보 손실이 가장 작은 것을 고른다.
#      정보 손실 = Σ (올린 단계 수 / 그 QI를 억제하기까지의 단계 수). 억제는 1.0.
#      그래도 같으면 QI 순서(core.schemas.QI_TYPES)상 뒤쪽 QI를 올린 조합을 고른다 (결정적 선택을 위한 규칙).
#   5) 부모가 뿌리(ANY)이거나 이미 공개된 node에 닿으면 더 올릴 곳이 없다. 그 다음 단계는 억제다.
#      ANY로 "일반화"해서 State에 넣지 않는다 (D1: ANY ≠ suppression).
#   전부 억제하면 State가 그대로이므로 k = k_before >= k_floor 다. 그래서 탐색은 반드시 끝난다.
#
# 아직 팀이 정하지 않은 값 (D1 "연구팀 결정 5")
#   정보 손실을 합으로 볼지 평균으로 볼지, 동점 규칙 — 여기서는 합과 위 4)의 규칙을 임시로 쓴다.
#
# 이 파일은 State를 바꾸지 않고, 정답(GT)을 보지 않는다.

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Mapping, Sequence

from core.schemas import QI_TYPES
from ontology.index import PopulationIndex


@dataclass(frozen=True)
class DeltaItem:
    """
    key       → 호출 측이 붙인 식별자 (observation_id)
    attribute → QI
    node_id   → 응답이 드러내려는 node
    """
    key: str
    attribute: str
    node_id: str


@dataclass(frozen=True)
class ItemPlan:
    """steps: 올린 단계 수 (0 = 그대로). released_node가 None이면 억제."""
    key: str
    attribute: str
    original_node: str
    released_node: str | None
    steps: int
    max_steps: int

    @property
    def suppressed(self) -> bool:
        return self.released_node is None

    @property
    def generalized(self) -> bool:
        return self.released_node is not None and self.steps > 0


@dataclass(frozen=True)
class DefensePlan:
    """
    items               → QI별 결정
    k_before / k_raw    → 지금 k / 그대로 공개했을 때 k
    k_floor / k_planned → 하한 / 계획대로 공개했을 때 k
    intervened          → 하나라도 올리거나 억제했는지
    frontier_depth      → 안전한 조합을 찾은 층 (올린 단계 수의 합). 손대지 않았으면 0
    configs_examined    → 시험한 조합 수
    information_loss    → 고른 조합의 정보 손실
    reason              → 개입한 이유 ("BELOW_FLOOR" / "NEW_ZERO_MATCH"), 개입하지 않았으면 None
    """
    items: tuple[ItemPlan, ...]
    k_before: int
    k_raw: int
    k_floor: int
    k_planned: int
    intervened: bool
    frontier_depth: int
    configs_examined: int
    information_loss: float
    reason: str | None


def plan_release(
    index: PopulationIndex,
    state: Mapping[str, str],
    delta: Sequence[DeltaItem],
    *,
    k_req: int,
) -> DefensePlan:
    """
    input : index (기준 집단 색인), state ({QI → 이미 공개된 node}), delta (새 공개분. QI당 하나),
            k_req (요구 익명성. 하한은 min(k_req, 지금 k))
    output : DefensePlan
    """
    attributes = [item.attribute for item in delta]
    if len(set(attributes)) != len(attributes):
        raise ValueError("delta에는 QI당 관찰 하나만 넣어야 합니다.")
    for item in delta:
        existing = state.get(item.attribute)
        if existing is not None and index.ontology.relation(existing, item.node_id) != "DESCENDANT":
            raise ValueError(f"{item.attribute}: 새 공개분이 아닙니다 (이미 공개된 {existing}와 같거나 넓거나 양립하지 않는 값).")

    k_before = index.count(state)
    k_floor = min(k_req, k_before)

    # QI별로 내놓을 수 있는 node를 구체적인 것부터 나열한다. 마지막 칸 None이 억제다.
    ladders: list[list[str | None]] = [_ladder(index, state, item) for item in delta]

    def k_of(config: Sequence[int]) -> int:
        constraints = dict(state)
        for item, ladder, step in zip(delta, ladders, config):
            node = ladder[step]
            if node is not None:
                constraints[item.attribute] = node
        return index.count(constraints)

    def safe(k: int) -> bool:
        return k >= k_floor and not (k == 0 and k_before > 0)

    raw_config = tuple(0 for _ in delta)
    k_raw = k_of(raw_config)
    if not delta or safe(k_raw):
        return _result(delta, ladders, raw_config, k_before, k_raw, k_floor, k_raw, 0, 1, None)

    reason = "NEW_ZERO_MATCH" if k_raw == 0 else "BELOW_FLOOR"
    max_steps = [len(ladder) - 1 for ladder in ladders]
    order = {qi: position for position, qi in enumerate(QI_TYPES)}
    examined = 1

    for depth in range(1, sum(max_steps) + 1):
        best: tuple[tuple[float, tuple[int, ...]], tuple[int, ...], int] | None = None
        for config in product(*(range(limit + 1) for limit in max_steps)):
            if sum(config) != depth:
                continue
            examined += 1
            k = k_of(config)
            if not safe(k):
                continue
            loss = sum(step / limit for step, limit in zip(config, max_steps) if limit)
            # 동점이면 QI 순서상 뒤쪽을 올린 조합이 먼저 오도록 정렬 키를 만든다
            tie = tuple(config[i] for i in sorted(range(len(delta)), key=lambda i: order[delta[i].attribute]))
            key = (round(loss, 9), tie)
            if best is None or key < best[0]:
                best = (key, config, k)
        if best is not None:
            _, config, k = best
            return _result(delta, ladders, config, k_before, k_raw, k_floor, k, depth, examined, reason)

    raise AssertionError("전부 억제한 조합은 항상 안전하므로 여기에 도달할 수 없습니다.")


def _ladder(index: PopulationIndex, state: Mapping[str, str], item: DeltaItem) -> list[str | None]:
    """item.node_id에서 위로 올라가며 내놓을 수 있는 node들. 뿌리나 이미 공개된 node 앞에서 멈추고 None(억제)을 붙인다."""
    ontology = index.ontology
    stop = state.get(item.attribute)
    ladder: list[str | None] = [item.node_id]
    for ancestor in ontology.ancestors(item.node_id):
        if ancestor == stop or ontology.node(ancestor).is_root:
            break
        ladder.append(ancestor)
    ladder.append(None)
    return ladder


def _result(delta, ladders, config, k_before, k_raw, k_floor, k_planned, depth, examined, reason) -> DefensePlan:
    items = tuple(
        ItemPlan(item.key, item.attribute, item.node_id, ladder[step], step, len(ladder) - 1)
        for item, ladder, step in zip(delta, ladders, config)
    )
    loss = sum(plan.steps / plan.max_steps for plan in items if plan.max_steps)
    return DefensePlan(
        items=items,
        k_before=k_before,
        k_raw=k_raw,
        k_floor=k_floor,
        k_planned=k_planned,
        intervened=any(plan.steps > 0 for plan in items),
        frontier_depth=depth,
        configs_examined=examined,
        information_loss=round(loss, 6),
        reason=reason,
    )
