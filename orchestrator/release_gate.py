# 공개 게이트: raw candidate를 어떤 모습으로 내보낼지 정한다
#
# 조건별 동작
#   C0 : 그대로 공개한다 (Defender 없음).
#   C1 : risk_defender 판정을 받는다.
#          개입 없음(allow)        → 그대로 공개
#          일반화·억제 필요         → Rewrite LLM으로 고쳐 씀 → 고친 응답에서 다시 추출 → k 재계산
#          하한을 만족하면 공개, 아니면 한 번 더 (max_defense_rounds까지)
#          끝내 만족하지 못하면     → 정해 둔 거절 문장을 공개 (새 QI 없음)
#
# 하한 (D1 Stage 11: k_floor = min(k_req, k_before))
#   공개 후 k가 k_floor 이상이어야 한다. 이미 k_req 아래인 State에서는 "더 좁히지 않는 것"이 기준이 된다.
#   risk_defender의 판정(k <= threshold면 위험)에는 이 하한이 없어, 최종 확인은 여기서 한다.
#
# 이 파일은 State를 바꾸지 않는다. session.project()·defend()만 부르고 commit은 session.py가 한다.
# 반환하는 released_observations는 반드시 released_response에서 뽑은 것이다 (D1: Released Response만 commit).

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from adapters.risk_adapter import DefenseOutcome, RiskSession
from normalizer.normalizer import NormalizedObservation, Normalizer
from orchestrator.rewriter import (
    ResponseRewriter,
    RewriteAction,
    RewriteError,
    RewriteInstruction,
    RewriteResult,
)
from qi_extractor.extractor import ExtractionError, QIExtractor
from qi_extractor.schema import RawObservation

DEFAULT_REFUSAL_TEXT = "죄송하지만 이 질문에는 답변드리기 어렵습니다."


class Condition(str, Enum):
    C0 = "C0"   # Defender 없음, 규칙 주입 없음
    C1 = "C1"   # 공개 전 Defender
    C2 = "C2"   # 생성 전 규칙 주입 (경량 언러닝 파트)
    C3 = "C3"   # 규칙 주입 + Defender


SUPPORTED_CONDITIONS = frozenset({Condition.C0, Condition.C1})


class ReleasePath(str, Enum):
    PASSTHROUGH = "PASSTHROUGH"   # C0: 검사 없이 공개
    ALLOWED = "ALLOWED"           # Defender 검사 통과, 고치지 않고 공개
    REWRITTEN = "REWRITTEN"       # 일반화·억제해 고쳐 쓴 응답을 공개
    REFUSAL = "REFUSAL"           # 거절 문장을 공개 (fallback)


class RefusalReason(str, Enum):
    RAW_EXTRACTION_FAILED = "RAW_EXTRACTION_FAILED"      # raw candidate 추출 실패 → 위험을 계산할 수 없음
    REWRITE_FAILED = "REWRITE_FAILED"                    # 고쳐 쓴 결과가 비었거나 잘림
    REEXTRACTION_FAILED = "REEXTRACTION_FAILED"          # 고쳐 쓴 응답의 추출 실패
    FLOOR_NOT_MET = "FLOOR_NOT_MET"                      # Defender가 개입할 것이 없다는데 하한 미달
    ROUNDS_EXHAUSTED = "ROUNDS_EXHAUSTED"                # 정해진 횟수 안에 하한을 만족하지 못함


@dataclass(frozen=True)
class GateConfig:
    """
    max_defense_rounds
    → 고쳐 쓰기 → 재확인을 몇 번까지 할지 (D1: Pilot 후 freeze)
    refusal_text
    → fallback으로 공개할 문장
    """
    max_defense_rounds: int = 2
    refusal_text: str = DEFAULT_REFUSAL_TEXT

    def __post_init__(self) -> None:
        if self.max_defense_rounds < 1:
            raise ValueError("max_defense_rounds는 1 이상이어야 합니다.")


@dataclass(frozen=True)
class DefenseRound:
    """고쳐 쓰기 한 번의 기록."""
    round_no: int
    defense: DefenseOutcome
    rewrite: RewriteResult | None
    k_projected: int | None          # 고쳐 쓴 응답을 공개했을 때 k (재추출 실패면 None)
    floor_satisfied: bool
    extraction_tokens: tuple[int, int]

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_no,
            "defense": self.defense.to_log_dict(),
            "rewrite": self.rewrite.to_log_dict() if self.rewrite else None,
            "k_projected": self.k_projected,
            "floor_satisfied": self.floor_satisfied,
            "extraction_input_tokens": self.extraction_tokens[0],
            "extraction_output_tokens": self.extraction_tokens[1],
        }


@dataclass(frozen=True)
class ReleaseDecision:
    """
    released_response / released_observations
    → 실제로 공개하는 응답과, 그 응답에서 뽑아 정규화한 관찰 (State commit 대상)
    path / refusal_reason
    → 어떤 경로로 공개됐는지, 거절이면 이유
    k_before / k_raw_projected / k_floor / k_released_projected
    → 턴 시작 k / raw를 그대로 냈을 때 k (C0에서도 계산해 둠) / 하한 / 공개분 기준 k
    generalized / suppressed
    → 최종 공개본에 일반화·억제가 적용됐는지 (evaluation 로그용)
    rounds
    → 고쳐 쓰기 기록
    """
    released_response: str
    released_observations: tuple[NormalizedObservation, ...]
    path: ReleasePath
    refusal_reason: RefusalReason | None
    defender_enabled: bool
    k_before: int
    k_raw_projected: int
    k_floor: int
    k_released_projected: int
    generalized: bool
    suppressed: bool
    rounds: tuple[DefenseRound, ...]

    @property
    def intervened(self) -> bool:
        return self.path in (ReleasePath.REWRITTEN, ReleasePath.REFUSAL)

    @property
    def floor_satisfied(self) -> bool:
        return floor_satisfied(self.k_before, self.k_released_projected, self.k_floor)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "released_response": self.released_response,
            "released_observations": [obs.to_log_dict() for obs in self.released_observations],
            "path": self.path.value,
            "refusal_reason": self.refusal_reason.value if self.refusal_reason else None,
            "defender_enabled": self.defender_enabled,
            "k_before": self.k_before,
            "k_raw_projected": self.k_raw_projected,
            "k_floor": self.k_floor,
            "k_released_projected": self.k_released_projected,
            "floor_satisfied": self.floor_satisfied,
            "generalized": self.generalized,
            "suppressed": self.suppressed,
            "rounds": [item.to_log_dict() for item in self.rounds],
        }


def floor_satisfied(k_before: int, k_after: int, k_floor: int) -> bool:
    """공개 후 k가 하한 이상이고, 새로 0명(ZERO_MATCH)이 되지 않았는지."""
    if k_after == 0 and k_before > 0:
        return False
    return k_after >= k_floor


class ReleaseGate:
    """추출기·정규화기·재작성기를 주입받는다. 세션마다 새로 만들 필요 없이 run 전체에서 하나를 쓴다."""

    def __init__(
        self,
        extractor: QIExtractor,
        normalizer: Normalizer,
        rewriter: ResponseRewriter,
        config: GateConfig = GateConfig(),
    ) -> None:
        self._extractor = extractor
        self._normalizer = normalizer
        self._rewriter = rewriter
        self._config = config

    def release(
        self,
        condition: Condition,
        session: RiskSession,
        *,
        query: str,
        raw_response: str,
        raw_observations: Sequence[RawObservation] | None,
        turn: int,
    ) -> ReleaseDecision:
        """
        input : condition, session (이 세션의 RiskSession), query, raw_response,
                raw_observations (raw candidate 추출 결과. 추출에 실패했으면 None), turn
        output : ReleaseDecision
        """
        if condition not in SUPPORTED_CONDITIONS:
            raise ValueError(f"{condition.value}는 아직 지원하지 않습니다 (경량 언러닝 파트 이후).")

        k_before = session.k_current
        k_floor = min(session.config.k_req, k_before)
        extraction_ok = raw_observations is not None
        raw_normalized = self._normalizer.normalize_all(raw_observations or ())
        k_raw = session.project(raw_normalized).k_after

        def decide(path: ReleasePath, text: str, observations: Sequence[NormalizedObservation],
                   k_released: int, *, reason: RefusalReason | None = None,
                   rounds: Sequence[DefenseRound] = (), defense: DefenseOutcome | None = None) -> ReleaseDecision:
            return ReleaseDecision(
                released_response=text,
                released_observations=tuple(observations),
                path=path,
                refusal_reason=reason,
                defender_enabled=condition is Condition.C1,
                k_before=k_before,
                k_raw_projected=k_raw,
                k_floor=k_floor,
                k_released_projected=k_released,
                generalized=path is ReleasePath.REWRITTEN and any(r.defense.generalized for r in rounds),
                suppressed=path is ReleasePath.REFUSAL
                or (path is ReleasePath.REWRITTEN and any(r.defense.suppressed_ids for r in rounds)),
                rounds=tuple(rounds),
            )

        def refuse(reason: RefusalReason, rounds: Sequence[DefenseRound] = ()) -> ReleaseDecision:
            return decide(ReleasePath.REFUSAL, self._config.refusal_text, (), k_before,
                          reason=reason, rounds=rounds)

        # ── C0: 검사 없이 공개 ──
        if condition is Condition.C0:
            return decide(ReleasePath.PASSTHROUGH, raw_response, raw_normalized, k_raw)

        # ── C1 ──
        if not extraction_ok:
            return refuse(RefusalReason.RAW_EXTRACTION_FAILED)   # 위험을 계산할 수 없으면 내보내지 않는다

        text = raw_response
        observations: Sequence[RawObservation] = raw_observations or ()
        normalized = raw_normalized
        k_projected = k_raw
        rounds: list[DefenseRound] = []

        for round_no in range(1, self._config.max_defense_rounds + 1):
            defense = session.defend(normalized)
            if not defense.intervened:
                break

            instructions = build_instructions(defense, observations)
            try:
                if not instructions:
                    raise RewriteError("Defender 판정을 추출 관찰과 연결하지 못했습니다.")
                rewrite = self._rewriter.rewrite(text, instructions)
            except RewriteError:
                rounds.append(DefenseRound(round_no, defense, None, None, False, (0, 0)))
                return refuse(RefusalReason.REWRITE_FAILED, rounds)

            try:
                extraction = self._extractor.extract(query, rewrite.text, source_turn=turn)
            except ExtractionError:
                rounds.append(DefenseRound(round_no, defense, rewrite, None, False, (0, 0)))
                return refuse(RefusalReason.REEXTRACTION_FAILED, rounds)

            text = rewrite.text
            observations = extraction.observations
            normalized = self._normalizer.normalize_all(observations)
            k_projected = session.project(normalized).k_after
            satisfied = floor_satisfied(k_before, k_projected, k_floor)
            rounds.append(DefenseRound(round_no, defense, rewrite, k_projected, satisfied,
                                       (extraction.input_tokens, extraction.output_tokens)))
            if satisfied:
                return decide(ReleasePath.REWRITTEN, text, normalized, k_projected, rounds=rounds)
        else:
            return refuse(RefusalReason.ROUNDS_EXHAUSTED, rounds)

        # Defender가 더 고칠 것이 없다고 한 경우: 하한을 직접 확인하고 공개한다
        if not floor_satisfied(k_before, k_projected, k_floor):
            return refuse(RefusalReason.FLOOR_NOT_MET, rounds)
        path = ReleasePath.REWRITTEN if rounds else ReleasePath.ALLOWED
        return decide(path, text, normalized, k_projected, rounds=rounds)


def build_instructions(defense: DefenseOutcome, observations: Sequence[RawObservation]) -> list[RewriteInstruction]:
    """
    input : defense (risk 어댑터의 Defender 판정), observations (그 판정의 대상이 된 추출 관찰)
    output : Rewrite LLM에 줄 수정 지시 (추출 순서 유지)

    risk_defender가 정한 값만 옮긴다. 일반화 수준을 여기서 다시 고르지 않는다.
    """
    replacement = {oid: new for oid, _, _, new in defense.generalized}
    suppressed = set(defense.suppressed_ids)

    instructions: list[RewriteInstruction] = []
    for obs in observations:
        if obs.observation_id in replacement:
            action, new_value = RewriteAction.GENERALIZE, replacement[obs.observation_id]
        elif obs.observation_id in suppressed:
            action, new_value = RewriteAction.SUPPRESS, None
        else:
            continue
        instructions.append(RewriteInstruction(
            observation_id=obs.observation_id,
            attribute=obs.attribute,
            action=action,
            raw_value=obs.raw_value,
            evidence=tuple(span.text for span in obs.evidence_spans),
            replacement=new_value,
        ))
    return instructions
