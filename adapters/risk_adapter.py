# risk_defender(이유진 파트) 연결 어댑터
#
# 하는 일
#   1) 기준 집단(원본 300 + 합성 299,700 = 30만 명)을 run 시작 때 한 번 적재한다.
#   2) 정규화된 관찰 중 State에 넣을 수 있는 것만 골라 risk_defender의 QIObservation으로 바꾼다.
#   3) risk_defender 함수를 그대로 호출한다.
#        project → evaluate_projected_risk   : 공개했다면 k가 얼마가 되는지 (State 변경 없음)
#        defend  → defend_candidate_qis      : 위험하면 어떤 QI를 일반화·억제할지 (State 변경 없음)
#        commit  → update_state + calculate_k: 실제로 공개된 응답의 관찰만 State에 반영
#
# 판정 기준을 하나로 맞춘 방법
#   risk_defender : k <= risk_threshold 이면 위험         (이유진 파트 README 결정값 3)
#   evaluation_v1 : 1 <= k < k_req 이면 위반              (이정민 파트 log_schema_v1)
#   두 식이 같은 경계를 쓰도록 k_req = risk_threshold + 1 로 넘긴다 (threshold 3 ⇔ k_req 4).
#   D1 Part III의 Primary는 k_req = 5 (= risk_threshold 4)라서, 어느 쪽으로 갈지는 팀 확인이 필요하다.
#
# confidence 기준
#   risk_defender.update_state는 0~1 실수 confidence를 0.5와 비교한다. 추출기는 1~5 정수를 쓴다.
#   1~5를 그대로 넘기면 전부 0.5보다 커서 기준이 무의미해지므로, 넘길 때는 confidence=None으로 두고
#   commit 기준(τ_commit)은 이 어댑터에서 먼저 적용한다. τ_commit 값은 D1에서 Pilot 후 freeze할 값이다.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from core.schemas import RISK_DEFENDER_QI_TYPES
from normalizer.normalizer import NormalizedObservation
from risk_defender.defender import defend_candidate_qis
from risk_defender.demo import DEFAULT_DATA_PATH, DEFAULT_SYNTH_PATH
from risk_defender.loader import PersonaRecord, load_expanded_population, load_population
from risk_defender.risk import calculate_k, evaluate_projected_risk
from risk_defender.schema import PrivacyState, QIObservation, update_state


# 이유진 파트 README "Threshold 선정 근거"에서 결정한 값 (30만 명 기준 실험 → 3)
DEFAULT_RISK_THRESHOLD = 3


# ──────────────────────────────────────────────────────────────
# 기준 집단
# ──────────────────────────────────────────────────────────────

class PopulationMode(str, Enum):
    BASE = "base"          # 원본 300명
    EXPANDED = "expanded"  # 원본 300 + 합성 299,700 = 30만 명 (이유진 파트 현재 기준)


@dataclass(frozen=True)
class ReferencePopulation:
    """
    records
    → risk_defender PersonaRecord 목록. 30만 명이면 약 500MB라 run 전체에서 하나만 만들어 공유한다.
    mode / base_path / synth_path
    → 어떤 데이터로 k를 계산했는지 (run 로그 기록용)
    """
    records: list[PersonaRecord] = field(repr=False)
    mode: PopulationMode
    base_path: str
    synth_path: str | None

    @property
    def size(self) -> int:
        return len(self.records)


class ReferenceDataMissingError(FileNotFoundError):
    """기준 집단 파일이 없을 때. 준비 명령을 안내한다."""


def load_reference_population(
    mode: PopulationMode = PopulationMode.EXPANDED,
    *,
    base_path: str | Path = DEFAULT_DATA_PATH,
    synth_path: str | Path = DEFAULT_SYNTH_PATH,
) -> ReferencePopulation:
    """
    input : mode (EXPANDED=30만 / BASE=300), 데이터 경로 (기본값은 risk_defender/data/ 아래)
    output : ReferencePopulation

    EXPANDED인데 합성 파일이 없으면 오류를 낸다. 조용히 300명으로 돌아가면
    k 값의 의미가 바뀐 채로 실험이 진행되기 때문이다.
    (risk_defender의 load_expanded_population은 합성 파일이 없으면 원본만 반환하므로 여기서 먼저 막는다.)
    """
    base_path = Path(base_path)
    synth = Path(synth_path)

    missing = [p for p in (base_path, synth) if not p.exists()
               and (p == base_path or mode is PopulationMode.EXPANDED)]
    if missing:
        raise ReferenceDataMissingError(
            "기준 집단 파일이 없습니다: " + ", ".join(str(p) for p in missing) + "\n"
            "프로젝트 루트에서 'python -m adapters.prepare_reference'를 먼저 실행하세요."
        )

    if mode is PopulationMode.EXPANDED:
        records = load_expanded_population(base_path, synth_path=synth)
        return ReferencePopulation(records, mode, str(base_path), str(synth))

    records = load_population(base_path)
    return ReferencePopulation(records, mode, str(base_path), None)


# ──────────────────────────────────────────────────────────────
# 설정 · 결과 객체
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RiskConfig:
    """
    risk_threshold
    → risk_defender 기준: k <= 이 값이면 위험 (기본 3, 이유진 파트 결정값)
    tau_commit
    → 이 confidence(1~5) 미만 관찰은 State에 넣지 않음. None이면 confidence로 거르지 않음 (Pilot 전 임시)
    """
    risk_threshold: int = DEFAULT_RISK_THRESHOLD
    tau_commit: int | None = None

    def __post_init__(self) -> None:
        if self.risk_threshold < 1:
            raise ValueError("risk_threshold는 1 이상이어야 합니다.")
        if self.tau_commit is not None and not 1 <= self.tau_commit <= 5:
            raise ValueError("tau_commit은 1~5 정수 또는 None이어야 합니다.")

    @property
    def k_req(self) -> int:
        """evaluation 로그(1 <= k < k_req)에 넘길 값. risk_defender 판정과 같은 경계가 된다."""
        return self.risk_threshold + 1


class ExclusionReason(str, Enum):
    NOT_MAPPED = "NOT_MAPPED"                    # PENDING·UNMAPPED
    OUT_OF_RISK_SCOPE = "OUT_OF_RISK_SCOPE"      # risk_defender가 다루지 않는 QI
    BELOW_TAU_COMMIT = "BELOW_TAU_COMMIT"        # confidence < τ_commit


@dataclass(frozen=True)
class Exclusion:
    observation_id: str
    attribute: str
    reason: ExclusionReason


@dataclass(frozen=True)
class Selection:
    """State 계산에 쓸 관찰(accepted)과 빠진 관찰(excluded)."""
    accepted: tuple[QIObservation, ...]
    excluded: tuple[Exclusion, ...]

    @property
    def accepted_ids(self) -> tuple[str, ...]:
        return tuple(qi.source for qi in self.accepted)


@dataclass(frozen=True)
class RiskProjection:
    """공개했다고 가정했을 때의 k 변화. State는 바뀌지 않는다."""
    k_before: int
    k_after: int
    risky: bool                  # risk_defender 판정 (k_after <= threshold, 또는 k_after == 0)
    selection: Selection

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "k_before": self.k_before,
            "k_after": self.k_after,
            "risky": self.risky,
            "accepted_ids": list(self.selection.accepted_ids),
            "excluded": [vars(e) | {"reason": e.reason.value} for e in self.selection.excluded],
        }


@dataclass(frozen=True)
class DefenseOutcome:
    """
    risk_defender.defend_candidate_qis 결과를 observation_id 기준으로 다시 정리한 것.

    action
    → "allow" / "generalize" / "suppress" (risk_defender 반환값 그대로)
    allowed_ids
    → 그대로 공개해도 되는 관찰
    generalized
    → (observation_id, attribute, 원래 값, 일반화 값) 목록. 응답 문장을 이 값으로 바꿔 써야 한다 (Part 6)
    suppressed_ids
    → 응답에서 빼야 하는 관찰
    k_before / k_raw / k_final
    → 방어 전 State의 k / 그대로 공개했을 때 k / 방어 결과대로 공개했을 때 k
    """
    action: str
    allowed_ids: tuple[str, ...]
    generalized: tuple[tuple[str, str, str, str], ...]
    suppressed_ids: tuple[str, ...]
    k_before: int
    k_raw: int
    k_final: int
    selection: Selection
    defense_log: tuple[dict[str, Any], ...]

    @property
    def intervened(self) -> bool:
        return bool(self.generalized or self.suppressed_ids)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "intervened": self.intervened,
            "allowed_ids": list(self.allowed_ids),
            "generalized": [
                {"observation_id": oid, "attribute": attr, "from": old, "to": new}
                for oid, attr, old, new in self.generalized
            ],
            "suppressed_ids": list(self.suppressed_ids),
            "k_before": self.k_before,
            "k_raw": self.k_raw,
            "k_final": self.k_final,
            "excluded": [vars(e) | {"reason": e.reason.value} for e in self.selection.excluded],
            "defense_log": list(self.defense_log),
        }


@dataclass(frozen=True)
class CommitOutcome:
    """실제로 공개된 응답의 관찰을 State에 반영한 결과."""
    k_before: int
    k_after: int
    committed_ids: tuple[str, ...]
    new_conflicts: int           # 이번 commit으로 새로 생긴 충돌 기록 수 (risk_defender conflicts)
    state_after: dict[str, Any]  # risk_defender committed_qis() 결과
    selection: Selection

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "k_before": self.k_before,
            "k_after": self.k_after,
            "committed_ids": list(self.committed_ids),
            "new_conflicts": self.new_conflicts,
            "state_after": dict(self.state_after),
            "excluded": [vars(e) | {"reason": e.reason.value} for e in self.selection.excluded],
        }


# ──────────────────────────────────────────────────────────────
# 세션
# ──────────────────────────────────────────────────────────────

class RiskSession:
    """
    target 한 명과의 대화 세션 하나에 대응한다. risk_defender PrivacyState를 하나 들고 있다.

    State를 바꾸는 메서드는 commit() 하나뿐이다.
    project()·defend()는 "공개한다면"을 계산만 하고 State를 건드리지 않는다.
    Raw Candidate가 State에 먼저 들어갔다가 되돌려지는 일을 구조적으로 막기 위함이다 (D1 Stage 11).
    """

    def __init__(
        self,
        population: ReferencePopulation,
        target_id: str,
        session_id: str,
        config: RiskConfig = RiskConfig(),
    ) -> None:
        self._population = population
        self._config = config
        self._state = PrivacyState(target_id=target_id, session_id=session_id)
        self._k_current = calculate_k(population.records, self._state).k

    # ── 조회 ─────────────────────────────────────────────

    @property
    def config(self) -> RiskConfig:
        return self._config

    @property
    def k_current(self) -> int:
        """현재 State 기준 후보 수 (k_run). 빈 State에서는 기준 집단 크기."""
        return self._k_current

    @property
    def committed_qis(self) -> dict[str, Any]:
        """현재 State에 commit된 QI (복사본)."""
        return dict(self._state.committed_qis())

    @property
    def conflict_count(self) -> int:
        return len(self._state.conflicts)

    # ── 선별 ─────────────────────────────────────────────

    def select(self, observations: Sequence[NormalizedObservation]) -> Selection:
        """
        input : 정규화된 관찰 목록
        output : Selection

        risk_defender로 넘길 수 있는 관찰만 고른다.
          MAPPED 아님 → 제외 / SEX·INC·POB → 제외 / confidence < τ_commit → 제외
        넘길 때 source에 observation_id를 넣어, 방어 결과를 원래 관찰로 되짚을 수 있게 한다.
        """
        accepted: list[QIObservation] = []
        excluded: list[Exclusion] = []
        for obs in observations:
            reason = self._exclusion_reason(obs)
            if reason is not None:
                excluded.append(Exclusion(obs.observation_id, obs.attribute, reason))
                continue
            accepted.append(QIObservation(
                qi_type=obs.attribute,
                value=obs.canonical_value,
                source_turn=obs.source_turn,
                confidence=None,          # 1~5 척도를 0~1 기준과 섞지 않음 (파일 상단 설명)
                source=obs.observation_id,
            ))
        return Selection(tuple(accepted), tuple(excluded))

    def _exclusion_reason(self, obs: NormalizedObservation) -> ExclusionReason | None:
        if obs.attribute not in RISK_DEFENDER_QI_TYPES:
            return ExclusionReason.OUT_OF_RISK_SCOPE
        if not obs.is_mapped or not obs.canonical_value:
            return ExclusionReason.NOT_MAPPED
        if self._config.tau_commit is not None and obs.confidence < self._config.tau_commit:
            return ExclusionReason.BELOW_TAU_COMMIT
        return None

    # ── 계산 (State 변경 없음) ──────────────────────────

    def project(self, observations: Sequence[NormalizedObservation]) -> RiskProjection:
        """
        input : 정규화된 관찰 목록 (보통 raw candidate에서 뽑은 것)
        output : RiskProjection
        """
        selection = self.select(observations)
        if not selection.accepted:
            return RiskProjection(self._k_current, self._k_current,
                                  self._is_risky(self._k_current), selection)

        result = evaluate_projected_risk(
            self._population.records, self._state, list(selection.accepted),
            threshold=self._config.risk_threshold,
        )
        return RiskProjection(result.k_before, result.k_after, result.risky, selection)

    def defend(self, observations: Sequence[NormalizedObservation]) -> DefenseOutcome:
        """
        input : 정규화된 관찰 목록 (raw candidate에서 뽑은 것)
        output : DefenseOutcome

        넘길 관찰이 없으면 risk_defender를 부르지 않고 allow로 끝낸다.
        (risk_defender는 이미 위험한 State에 빈 목록을 넘기면 개입할 대상이 없는데도 'generalize'를 반환한다.)
        """
        selection = self.select(observations)
        if not selection.accepted:
            return DefenseOutcome("allow", (), (), (), self._k_current, self._k_current,
                                  self._k_current, selection, ())

        decision = defend_candidate_qis(
            self._population.records, self._state, list(selection.accepted),
            threshold=self._config.risk_threshold,
        )

        # risk_defender는 일반화 결과를 {qi_type, from, to} dict로만 돌려준다.
        # (qi_type, 원래 값)으로 우리 observation_id를 되찾는다.
        id_by_value = {(qi.qi_type, qi.value): qi.source for qi in selection.accepted}
        generalized = tuple(
            (id_by_value.get((item["qi_type"], item["from"]), ""), item["qi_type"], item["from"], item["to"])
            for item in decision.generalized_qis
        )
        return DefenseOutcome(
            action=decision.action,
            allowed_ids=tuple(qi.source for qi in decision.safe_qis),
            generalized=generalized,
            suppressed_ids=tuple(qi.source for qi in decision.suppressed_qis),
            k_before=decision.projected.k_before,
            k_raw=decision.projected.k_after,
            k_final=decision.final_k,
            selection=selection,
            defense_log=tuple(decision.log),
        )

    # ── 반영 (State 변경) ───────────────────────────────

    def commit(self, observations: Sequence[NormalizedObservation]) -> CommitOutcome:
        """
        input : 정규화된 관찰 목록. 반드시 실제로 공개된 응답(released_response)에서 뽑은 것이어야 한다
        output : CommitOutcome

        같은 QI의 구체화·충돌 처리는 risk_defender.update_state 규칙을 그대로 따른다.
        """
        selection = self.select(observations)
        k_before = self._k_current
        conflicts_before = len(self._state.conflicts)

        if selection.accepted:
            self._state = update_state(self._state, list(selection.accepted))
            self._k_current = calculate_k(self._population.records, self._state).k

        return CommitOutcome(
            k_before=k_before,
            k_after=self._k_current,
            committed_ids=selection.accepted_ids,
            new_conflicts=len(self._state.conflicts) - conflicts_before,
            state_after=self.committed_qis,
            selection=selection,
        )

    def _is_risky(self, k: int) -> bool:
        """risk_defender evaluate_projected_risk와 같은 판정 (k == 0 도 위험으로 본다)."""
        return k <= self._config.risk_threshold if k > 0 else True
