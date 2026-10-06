# 온톨로지 기반 위험 계산 백엔드 (D1 Stage 3·4·5·11)
#
# adapters/risk_adapter.py의 RiskSession과 같은 방법(project·defend·commit …)을 제공한다.
# orchestrator는 어느 쪽을 쓰는지 몰라도 된다. 다른 점은 아래와 같다.
#
#                     risk_defender 백엔드 (이유진 파트)      온톨로지 백엔드 (이 파일)
#   k 계산 대상 QI    AGE·LOC·OCCP·EDU·REL (5개)             8개 전부
#   값 형식           문자열 ("30s", "Zurich, Switzerland")   node_id ("AGE:10Y30", "LOC:CH:zurich")
#   계층              risk_defender 내부 규칙                  ontology 패키지 (D1 Stage 3)
#   Defender          기준 2개 (일반화 5 / 억제 3)             하한 k_floor = min(k_req, 지금 k) + 한 단계씩 올리는 탐색
#   0명(ZERO_MATCH)   위험으로 판정                            새로 0명이 되는 공개는 막는다 (D1 Raw 공개 조건)
#
# 이 백엔드는 risk_defender를 대신하려는 것이 아니다. POC 보고서 §17.9가 말한 Design Pilot에서
# "지금 방식"과 "D1 설계 방식"을 같은 조건으로 비교할 수 있게 하는 두 번째 선택지다.
#
# State 갱신 규칙 (D1 Stage 4)
#   처음 나온 QI        → 그대로 넣는다 (NEW)
#   더 구체적인 node    → 바꾼다 (DESCENDANT)
#   같은 node / 더 넓은 node → 그대로 둔다 (SAME / ANCESTOR). 넓은 표현이 과거의 구체 정보를 지우지 않는다
#   양립하지 않는 node  → 그대로 두고 충돌로 기록한다 (INCOMPATIBLE)

from __future__ import annotations

from typing import Any, Sequence

from adapters.risk_adapter import (
    CommitOutcome,
    DefenseOutcome,
    Exclusion,
    ExclusionReason,
    ReferencePopulation,
    RiskConfig,
    RiskProjection,
    Selection,
)
from core.schemas import QI_TYPES
from normalizer.normalizer import NormalizedObservation
from ontology.defender import DeltaItem, plan_release
from ontology.index import PopulationIndex
from risk_defender.schema import QIObservation

BACKEND_NAME = "ontology_d1"


def build_population_index(population: ReferencePopulation) -> PopulationIndex:
    """
    input : population (risk 어댑터가 적재한 기준 집단. 각 레코드의 raw_profile을 쓴다)
    output : PopulationIndex

    30만 명 기준 수 초가 걸린다. run 시작 때 한 번만 만든다.
    """
    return PopulationIndex.build([record.raw_profile for record in population.records])


class OntologyRiskSession:
    backend_name = BACKEND_NAME

    def __init__(self, index: PopulationIndex, target_id: str, session_id: str,
                 config: RiskConfig = RiskConfig()) -> None:
        self._index = index
        self._ontology = index.ontology
        self._config = config
        self._target_id = target_id
        self._session_id = session_id
        self._state: dict[str, str] = {}
        self._conflicts: list[dict[str, Any]] = []
        self._k_current = index.count(self._state)

    # ── 조회 ─────────────────────────────────────────────

    @property
    def config(self) -> RiskConfig:
        return self._config

    @property
    def k_current(self) -> int:
        return self._k_current

    @property
    def committed_qis(self) -> dict[str, Any]:
        """현재 State {QI → node_id} (QI_TYPES 순서, 복사본)."""
        return {qi: self._state[qi] for qi in QI_TYPES if qi in self._state}

    @property
    def conflict_count(self) -> int:
        return len(self._conflicts)

    @property
    def refinable_qis(self) -> frozenset[str]:
        """공개된 node 아래에 더 구체적인 node가 있는 QI (O2 Seed의 적용 조건)."""
        return frozenset(qi for qi, node_id in self._state.items() if self._ontology.has_descendant(node_id))

    def display_value(self, attribute: str, value: Any) -> str:
        node_id = str(value)
        return self._ontology.node(node_id).phrase if node_id in self._ontology else node_id

    def relation(self, attribute: str, existing: Any, candidate: Any) -> str:
        """둘 중 하나라도 온톨로지 node가 아니면(정규화되지 않은 표현 등) 비교할 수 없으므로 INCOMPATIBLE로 본다."""
        existing, candidate = str(existing), str(candidate)
        if existing not in self._ontology or candidate not in self._ontology:
            return "SAME" if existing == candidate else "INCOMPATIBLE"
        return self._ontology.relation(existing, candidate)

    def fresh(self) -> "OntologyRiskSession":
        return OntologyRiskSession(self._index, self._target_id, self._session_id, self._config)

    # ── 선별 ─────────────────────────────────────────────

    def select(self, observations: Sequence[NormalizedObservation]) -> Selection:
        """MAPPED이고 온톨로지에 있는 node만 고른다. 8개 QI 모두 대상이다."""
        accepted: list[QIObservation] = []
        excluded: list[Exclusion] = []
        for obs in observations:
            if not obs.is_mapped or not obs.canonical_value or obs.canonical_value not in self._ontology:
                excluded.append(Exclusion(obs.observation_id, obs.attribute, ExclusionReason.NOT_MAPPED))
            elif self._config.tau_commit is not None and obs.confidence < self._config.tau_commit:
                excluded.append(Exclusion(obs.observation_id, obs.attribute, ExclusionReason.BELOW_TAU_COMMIT))
            else:
                accepted.append(QIObservation(obs.attribute, obs.canonical_value, obs.source_turn,
                                              None, obs.observation_id))
        return Selection(tuple(accepted), tuple(excluded))

    # ── 계산 (State 변경 없음) ──────────────────────────

    def project(self, observations: Sequence[NormalizedObservation]) -> RiskProjection:
        selection = self.select(observations)
        state, _, _ = self._apply(self._state, selection.accepted)
        k_after = self._index.count(state)
        return RiskProjection(self._k_current, k_after, self._violates(k_after), selection)

    def defend(self, observations: Sequence[NormalizedObservation]) -> DefenseOutcome:
        """
        input : 정규화된 관찰 목록 (raw candidate에서 뽑은 것)
        output : DefenseOutcome (generalized의 값은 node_id, replacement_text에 응답에 쓸 표현)
        """
        selection = self.select(observations)
        _, delta_by_qi, conflicts = self._apply(self._state, selection.accepted)

        delta = [DeltaItem(qi.source or "", qi.qi_type, str(qi.value)) for qi in delta_by_qi.values()]
        plan = plan_release(self._index, self._state, delta, k_req=self._config.k_req)

        plan_by_qi = {item.attribute: item for item in plan.items}
        conflict_ids = [qi.source or "" for qi in conflicts]
        allowed: list[str] = []
        generalized: list[tuple[str, str, str, str]] = []
        suppressed: list[str] = list(conflict_ids)
        replacement_text: dict[str, str] = {}

        for qi in selection.accepted:
            key, node_id = qi.source or "", str(qi.value)
            if key in conflict_ids:
                continue
            item = plan_by_qi.get(qi.qi_type)
            existing = self._state.get(qi.qi_type)
            already_known = existing is not None and self._ontology.is_same_or_descendant(existing, node_id)
            if item is None or item.steps == 0 or already_known:
                allowed.append(key)             # 그대로 공개해도 되는 값이거나, 이미 공개된 값과 같거나 더 넓은 값
            elif item.released_node is None:
                suppressed.append(key)
            elif self._ontology.is_same_or_descendant(item.released_node, node_id):
                allowed.append(key)             # 같은 응답 안의 더 넓은 표현 (정해진 공개 수준 이내)
            else:
                # 한 응답에 같은 QI가 여러 번 나오면 모두 같은 공개 수준으로 맞춘다
                generalized.append((key, qi.qi_type, node_id, item.released_node))
                replacement_text[key] = self._ontology.node(item.released_node).phrase

        if not generalized and not suppressed:
            action = "allow"
        elif generalized:
            action = "generalize"
        else:
            action = "suppress"

        log = ({
            "policy": "d1_one_parent_frontier",
            "k_req": self._config.k_req,
            "k_floor": plan.k_floor,
            "intervention_reason": plan.reason or ("CONFLICT" if conflict_ids else None),
            "frontier_depth": plan.frontier_depth,
            "configs_examined": plan.configs_examined,
            "information_loss": plan.information_loss,
            "conflict_ids": conflict_ids,
            "items": [{"observation_id": item.key, "attribute": item.attribute, "from": item.original_node,
                       "to": item.released_node, "steps": item.steps, "max_steps": item.max_steps}
                      for item in plan.items],
        },)
        return DefenseOutcome(
            action=action,
            allowed_ids=tuple(allowed),
            generalized=tuple(generalized),
            suppressed_ids=tuple(suppressed),
            k_before=plan.k_before,
            k_raw=plan.k_raw,
            k_final=plan.k_planned,
            selection=selection,
            defense_log=log,
            replacement_text=replacement_text,
        )

    # ── 반영 (State 변경) ───────────────────────────────

    def commit(self, observations: Sequence[NormalizedObservation]) -> CommitOutcome:
        """반드시 실제로 공개된 응답에서 뽑은 관찰만 넘긴다."""
        selection = self.select(observations)
        k_before = self._k_current
        state, _, conflicts = self._apply(self._state, selection.accepted)

        self._state = state
        for qi in conflicts:
            self._conflicts.append({"qi_type": qi.qi_type, "value": qi.value, "source_turn": qi.source_turn,
                                    "source": qi.source, "existing_value": state.get(qi.qi_type)})
        self._k_current = self._index.count(state)

        return CommitOutcome(
            k_before=k_before,
            k_after=self._k_current,
            committed_ids=selection.accepted_ids,
            new_conflicts=len(conflicts),
            state_after=self.committed_qis,
            selection=selection,
        )

    # ── 내부 ─────────────────────────────────────────────

    def _apply(self, state: dict[str, str], accepted: Sequence[QIObservation],
               ) -> tuple[dict[str, str], dict[str, QIObservation], list[QIObservation]]:
        """
        input : state, accepted (선별된 관찰)
        output : (갱신된 State 사본, QI별 새 공개분, 충돌한 관찰)

        한 응답에 같은 QI가 여러 번 나오면 차례로 적용한다. 새 공개분은 QI당 마지막으로 State를 바꾼 관찰이다.
        """
        updated = dict(state)
        delta: dict[str, QIObservation] = {}
        conflicts: list[QIObservation] = []
        for qi in accepted:
            node_id = str(qi.value)
            existing = updated.get(qi.qi_type)
            relation = "NEW" if existing is None else self._ontology.relation(existing, node_id)
            if relation in ("NEW", "DESCENDANT"):
                updated[qi.qi_type] = node_id
                delta[qi.qi_type] = qi
            elif relation == "INCOMPATIBLE":
                conflicts.append(qi)
        return updated, delta, conflicts

    def _violates(self, k: int) -> bool:
        """요구 익명성 위반(1 <= k < k_req) 또는 0명."""
        return k < self._config.k_req
