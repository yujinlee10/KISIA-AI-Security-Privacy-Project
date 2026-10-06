# 세션 한 개의 턴 루프: target 한 명 × 조건 하나
#
# 세션마다 새로 만드는 것 : 위험 계산 세션(State), FuzzerAdapter(공격 진행 상태), DisclosureLedger, CoverageRegistry
# run 전체에서 공유하는 것 : 기준 집단, Target, Extractor, Normalizer, 공개 게이트 (SessionParts)
#
# 정보 경계 (D1 Part III §2)
#   Fuzzer 어댑터로 가는 것 : 공개된 QI 요약(AttackStateView)과 commit 전후 k (전략 선택용, LLM에는 안 감)
#   가지 않는 것             : raw candidate, raw candidate에서 뽑은 관찰, 정답(GT)
#   State에 들어가는 것      : 공개된 응답에서 뽑은 관찰만
#
# 종료 사유 (StopReason)
#   MAX_TURNS            : 정한 턴 수를 채움
#   NO_APPLICABLE_ACTION : 지금 State에 맞는 Seed가 없음
#   LLM_ERROR            : 재시도 후에도 LLM 호출 실패. 그 턴은 기록하지 않고 세션을 멈춘다
#   위험 기준 도달은 종료 사유가 아니다 (D1 Stage 6: Threshold와 Stopping Rule은 별개).

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from adapters.fuzzer_adapter import AttackStep, FuzzerAdapter, NoApplicableSeedError
from adapters.risk_adapter import CommitOutcome, RiskBackend
from attack.selector import AttackStateView
from core.llm_client import LLMError
from normalizer.normalizer import NormalizedObservation
from orchestrator.coverage import CoverageEvent, CoverageRegistry
from orchestrator.disclosure import DisclosureLedger, qi_relation, state_id
from orchestrator.release_gate import Arm, Condition, ObservationNormalizer, ReleaseDecision, ReleaseGate
from qi_extractor.extractor import ExtractionError, QIExtractor
from qi_extractor.schema import ExtractionResult
from target_llm.responder import TargetOutput, TargetResponder


class StopReason(str, Enum):
    MAX_TURNS = "MAX_TURNS"
    NO_APPLICABLE_ACTION = "NO_APPLICABLE_ACTION"
    LLM_ERROR = "LLM_ERROR"


@dataclass(frozen=True)
class SessionParts:
    """run 전체에서 공유하는 구성 요소. 모두 State를 갖지 않는다."""
    target: TargetResponder
    extractor: QIExtractor
    normalizer: ObservationNormalizer
    gate: ReleaseGate


@dataclass(frozen=True)
class TurnRecord:
    """
    한 턴의 전체 기록 (trusted 로그용. raw candidate가 들어 있으므로 Fuzzer 쪽으로 넘기지 않는다).

    attack                  → 질문과 그것이 만들어진 과정
    target                  → Target 호출 결과 (raw candidate 포함)
    raw_extraction          → raw candidate 추출 결과. 추출 실패면 None
    raw_extraction_error    → 추출 실패 메시지
    raw_normalized          → raw candidate 관찰의 정규화 결과 (위험 계산용)
    release                 → 공개 게이트 결정 (공개된 응답·관찰 포함)
    commit                  → State 반영 결과 (k_run 전후)
    committed_before        → 턴 시작 때의 State (턴 뒤의 State는 commit.state_after)
    disclosed_before/after  → 턴 전후 공개 상태 (State + State에 못 들어간 공개분)
    attack_view             → 질문을 만들 때 공격 쪽에 보여 준 공개 상태 (사람이 읽는 표현)
    coverage                → 이 턴의 Coverage 판정 (D1 Stage 9)
    relation                → 이번 턴 target_qi의 QI 관계 (NEW/SAME/DESCENDANT/ANCESTOR/INCOMPATIBLE)
    feedback_status         → 한재이 feedback 판정 (다음 턴 전략의 근거)
    """
    turn: int
    attack: AttackStep
    target: TargetOutput
    raw_extraction: ExtractionResult | None
    raw_extraction_error: str | None
    raw_normalized: tuple[NormalizedObservation, ...]
    release: ReleaseDecision
    commit: CommitOutcome
    committed_before: dict[str, Any]
    disclosed_before: dict[str, str]
    disclosed_after: dict[str, str]
    attack_view: dict[str, str]
    coverage: CoverageEvent
    relation: str
    feedback_status: str

    # State ID는 State에 commit된 값만으로 만든다 (D1: canonical committed QI만 StateKey에 포함).
    # Coverage의 State 수와 같은 기준이다. 정규화하지 못한 공개분은 disclosed_*에만 남는다.
    @property
    def state_id_before(self) -> str:
        return state_id(self.committed_before)

    @property
    def state_id_after(self) -> str:
        return state_id(self.commit.state_after)

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "attack": self.attack.to_log_dict(),
            "target": self.target.to_log_dict(),
            "raw_extraction": self.raw_extraction.to_log_dict() if self.raw_extraction else None,
            "raw_extraction_error": self.raw_extraction_error,
            "raw_normalized": [obs.to_log_dict() for obs in self.raw_normalized],
            "release": self.release.to_log_dict(),
            "commit": self.commit.to_log_dict(),
            "disclosed_before": self.disclosed_before,
            "disclosed_after": self.disclosed_after,
            "attack_view": self.attack_view,
            "coverage": self.coverage.to_log_dict(),
            "state_id_before": self.state_id_before,
            "state_id_after": self.state_id_after,
            "qi_relation": self.relation,
            "feedback_status": self.feedback_status,
        }


@dataclass(frozen=True)
class SessionResult:
    target_id: str
    arm: Arm
    turns: tuple[TurnRecord, ...]
    stop_reason: StopReason
    stop_detail: str | None
    k_initial: int
    k_final: int
    coverage: dict[str, Any]

    @property
    def condition(self) -> Condition:
        return self.arm.condition

    @property
    def k_min(self) -> int:
        return min([self.k_initial, *(t.commit.k_after for t in self.turns)])

    @property
    def unresolved_released(self) -> int:
        """
        공개된 응답에서 추출됐지만 정규화하지 못해(PENDING·UNMAPPED) k에 반영되지 않은 관찰 수.
        이 수가 크면 k_run이 실제보다 크게 나오고 Defender도 그만큼 보지 못한 것이다 (POC 보고서 실험 3의 우회 문제).
        """
        return sum(1 for t in self.turns for obs in t.release.released_observations if not obs.is_mapped)

    def first_violation_turn(self, k_req: int) -> int | None:
        """k_run이 처음으로 1 <= k < k_req가 된 턴. 없으면 None."""
        for record in self.turns:
            if 1 <= record.commit.k_after < k_req:
                return record.turn
        return None


class SessionRunner:
    """
    사용법
        runner = SessionRunner(parts, risk_session, fuzzer, Arm(Condition.C1))
        result = runner.run("pers1", max_turns=5, on_turn=logger.log_turn)
    """

    def __init__(self, parts: SessionParts, risk_session: RiskBackend,
                 fuzzer: FuzzerAdapter, arm: Arm | Condition, *, restrict_seeds_to_counted_qis: bool = True) -> None:
        """
        restrict_seeds_to_counted_qis
        → 위험 계산이 세는 QI를 노리는 Seed만 고르게 할지. 끄면 8개 QI 전부가 대상이다
          (risk_defender 백엔드에서는 소득·성별·출생지 질문이 k를 줄이지 못하는 턴이 된다)
        """
        self._parts = parts
        self._risk = risk_session
        self._fuzzer = fuzzer
        self._arm = arm if isinstance(arm, Arm) else Arm(arm)
        self._seed_scope = risk_session.counted_qis if restrict_seeds_to_counted_qis else None
        self._ledger = DisclosureLedger()
        self._coverage = CoverageRegistry()

    def run(self, target_id: str, *, max_turns: int,
            on_turn: Callable[[TurnRecord], None] | None = None) -> SessionResult:
        """
        input : target_id, max_turns (Target 호출 상한), on_turn (턴이 끝날 때마다 불리는 함수. 로그 기록용)
        output : SessionResult

        턴마다 on_turn을 불러 바로 기록하므로, 중간에 멈춰도 그때까지의 턴은 파일에 남는다.
        """
        if max_turns < 1:
            raise ValueError("max_turns는 1 이상이어야 합니다.")

        k_initial = self._risk.k_current
        records: list[TurnRecord] = []
        stop_reason, stop_detail = StopReason.MAX_TURNS, None

        for turn in range(1, max_turns + 1):
            try:
                record = self._run_turn(target_id, turn)
            except NoApplicableSeedError as exc:
                stop_reason, stop_detail = StopReason.NO_APPLICABLE_ACTION, str(exc)
                break
            except LLMError as exc:
                # core.llm_client의 오류 메시지에는 API 키가 들어가지 않는다 (상태 코드·본문 앞부분만)
                stop_reason, stop_detail = StopReason.LLM_ERROR, f"{type(exc).__name__}: {exc}"
                break
            records.append(record)
            if on_turn is not None:
                on_turn(record)

        return SessionResult(target_id, self._arm, tuple(records), stop_reason, stop_detail,
                             k_initial, self._risk.k_current, self._coverage.summary())

    # ── 한 턴 ────────────────────────────────────────────

    def _run_turn(self, target_id: str, turn: int) -> TurnRecord:
        committed_before = self._risk.committed_qis
        disclosed_before = self._ledger.view(committed_before)

        # 1) 질문 생성 — 공개된 정보만 본다. State 값은 사람이 읽는 표현으로 바꿔 넘긴다 (Seed 문장에 들어간다)
        attack_view = {qi: (self._risk.display_value(qi, value) if qi in committed_before else value)
                       for qi, value in disclosed_before.items()}
        attack = self._fuzzer.next_query(AttackStateView(
            attack_view, prior_turns=turn - 1, refinable_qis=self._risk.refinable_qis,
            target_qi_scope=self._seed_scope,
            unresolved_qis=frozenset(qi for qi in disclosed_before if qi not in committed_before)))

        # 2) Target 응답 (raw candidate)
        target = self._parts.target.respond(target_id, attack.query)

        # 3) raw candidate 추출 — 위험 계산용. 파싱 실패는 '추출 실패'로 남기고 게이트가 처리한다
        raw_extraction: ExtractionResult | None = None
        raw_error: str | None = None
        try:
            raw_extraction = self._parts.extractor.extract(
                attack.query, target.raw_candidate_response,
                source_turn=turn, response_truncated=target.response_truncated,
            )
        except ExtractionError as exc:
            raw_error = str(exc)
        raw_observations = raw_extraction.observations if raw_extraction else None
        raw_normalized = self._parts.normalizer.normalize_all(raw_observations or ())

        # 4) 공개 게이트
        release = self._parts.gate.release(
            self._arm.condition, self._risk,
            query=attack.query, raw_response=target.raw_candidate_response,
            raw_observations=raw_observations, turn=turn, defender_mode=self._arm.defender_mode,
        )

        # 5) 공개된 응답의 관찰만 State에 반영
        commit = self._risk.commit(release.released_observations)
        self._ledger.record(release.released_observations, commit.committed_ids)
        committed_after = self._risk.committed_qis
        disclosed_after = self._ledger.view(committed_after)
        coverage = self._coverage.observe(committed_before, committed_after, commit.k_before, commit.k_after)

        # 6) 이번 턴의 k 변화를 Fuzzer에 알림 (전략 선택에만 쓰이고 Mutation LLM에는 가지 않는다)
        feedback = self._fuzzer.observe(commit.k_before, commit.k_after)

        return TurnRecord(
            turn=turn,
            attack=attack,
            target=target,
            raw_extraction=raw_extraction,
            raw_extraction_error=raw_error,
            raw_normalized=raw_normalized,
            release=release,
            commit=commit,
            committed_before=dict(committed_before),
            disclosed_before=disclosed_before,
            disclosed_after=disclosed_after,
            attack_view=attack_view,
            coverage=coverage,
            relation=qi_relation(attack.target_qi, committed_before, committed_after,
                                 disclosed_before, disclosed_after,
                                 release.released_observations, self._risk.relation),
            feedback_status=feedback.status,
        )
