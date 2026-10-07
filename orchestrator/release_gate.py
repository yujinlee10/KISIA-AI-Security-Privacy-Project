# 공개 게이트: raw candidate를 어떤 모습으로 내보낼지 정한다
#
# 조건별 동작
#   C0 : 그대로 공개한다 (Defender 없음).
#   C1 : Defender 판정을 받는다 (어느 백엔드든 같은 흐름).
#          개입 없음(allow)        → 그대로 공개
#          일반화·억제 필요         → Rewrite LLM으로 고쳐 씀 → 고친 응답에서 다시 추출 → k 재계산
#          하한을 만족하면 공개, 아니면 한 번 더 (max_defense_rounds까지)
#          끝내 만족하지 못하면     → 정해 둔 거절 문장을 공개 (새 QI 없음)
#
# 하한 (D1 Stage 11: k_floor = min(k_req, k_before))
#   공개 후 k가 k_floor 이상이어야 한다. 이미 k_req 아래인 State에서는 "더 좁히지 않는 것"이 기준이 된다.
#   Defender가 무엇을 고르든 최종 확인은 여기서 한다.
#
# Defender가 보는 State (DefenderMode)
#   STATEFUL  : 세션에 쌓인 State 위에서 판정한다 (기본).
#   STATELESS : 매 턴 빈 State에서 판정한다. 한 응답 안의 QI끼리는 함께 보지만 앞선 턴은 기억하지 않는다.
#               POC 보고서 실험 7의 무상태 대조군이다. 판정만 다르고, 실제 State와 k_run은 똑같이 쌓인다.
#
# 이 파일은 State를 바꾸지 않는다. project()·defend()만 부르고 commit은 session.py가 한다.
# 반환하는 released_observations는 반드시 released_response에서 뽑은 것이다 (D1: Released Response만 commit).

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol, Sequence

from adapters.risk_adapter import DefenseOutcome, RiskBackend
from normalizer.normalizer import MappingReason, MappingStatus, NormalizedObservation
from orchestrator.rewriter import (
    ResponseRewriter,
    RewriteAction,
    RewriteError,
    RewriteInstruction,
    RewriteResult,
)
from qi_extractor.evidence import locate_span
from qi_extractor.extractor import ExtractionError, QIExtractor
from qi_extractor.schema import RawObservation

DEFAULT_REFUSAL_TEXT = "죄송하지만 이 질문에는 답변드리기 어렵습니다."


class Condition(str, Enum):
    C0 = "C0"   # Defender 없음, 규칙 주입 없음
    C1 = "C1"   # 공개 전 Defender
    C2 = "C2"   # 생성 전 규칙 주입 (경량 언러닝 파트)
    C3 = "C3"   # 규칙 주입 + Defender


SUPPORTED_CONDITIONS = frozenset({Condition.C0, Condition.C1})


class DefenderMode(str, Enum):
    STATEFUL = "stateful"
    STATELESS = "stateless"


@dataclass(frozen=True)
class Arm:
    """
    실험에서 서로 비교하는 한 갈래: 조건 + Defender가 보는 State.

    label은 "C0", "C1", "C1S"(무상태 Defender) 세 가지다.
    evaluation 로그의 condition 컬럼에는 조건(C0/C1)이 들어가고, 무상태 여부는 defender 컬럼으로 구분된다.
    """
    condition: Condition
    defender_mode: DefenderMode = DefenderMode.STATEFUL

    def __post_init__(self) -> None:
        if self.condition not in SUPPORTED_CONDITIONS:
            raise ValueError(f"{self.condition.value}는 아직 지원하지 않습니다 (경량 언러닝 파트 이후).")
        if self.condition is Condition.C0 and self.defender_mode is DefenderMode.STATELESS:
            raise ValueError("C0에는 Defender가 없으므로 무상태 설정을 붙일 수 없습니다.")

    @property
    def label(self) -> str:
        return self.condition.value + ("S" if self.defender_mode is DefenderMode.STATELESS else "")

    @classmethod
    def parse(cls, label: str) -> "Arm":
        text = label.strip().upper()
        if text.endswith("S") and len(text) == 3:
            return cls(Condition(text[:2]), DefenderMode.STATELESS)
        return cls(Condition(text))


ARM_LABELS = ("C0", "C1", "C1S")


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


MIN_RESIDUAL_SPAN_CHARS = 6   # 이보다 짧은 근거 문구("his" 같은 낱말)는 다른 문맥에도 나오므로 잔여 검사에서 뺀다


class ObservationNormalizer(Protocol):
    """normalizer.Normalizer와 ontology.OntologyNormalizer가 이 모양을 만족한다."""

    def normalize_all(self, observations: Sequence[RawObservation]) -> tuple[NormalizedObservation, ...]:
        ...


@dataclass(frozen=True)
class GateConfig:
    """
    max_defense_rounds
    → 고쳐 쓰기 → 재확인을 몇 번까지 할지 (D1: Pilot 후 freeze)
    refusal_text
    → fallback으로 공개할 문장
    adopt_instructed_value
    → 고쳐 쓴 응답에서 일반화된 QI를 다시 추출했는데 그 표현을 규칙으로 읽지 못했을 때,
      Defender가 지시한 값으로 기록할지. 끄면 그 QI는 State에 들어가지 않는다 (공개됐는데 k에 반영되지 않음)
    check_residual_evidence
    → 지우라고 한 값의 근거 문구가 고쳐 쓴 응답에 글자 그대로 남아 있으면 그 회차를 통과시키지 않고 다시 고치게 할지.
      재추출이 놓친 단서를 막기 위한 것이다. 한 문구가 지울 값과 남길 값의 근거를 겸하면 남길 값의 근거도 함께 빠진다
    """
    max_defense_rounds: int = 2
    refusal_text: str = DEFAULT_REFUSAL_TEXT
    adopt_instructed_value: bool = True
    check_residual_evidence: bool = True

    def __post_init__(self) -> None:
        if self.max_defense_rounds < 1:
            raise ValueError("max_defense_rounds는 1 이상이어야 합니다.")


@dataclass(frozen=True)
class DefenseRound:
    """고쳐 쓰기 한 번의 기록."""
    round_no: int
    defense: DefenseOutcome
    rewrite: RewriteResult | None
    k_projected: int | None          # 고쳐 쓴 응답을 공개했을 때 k (Defender가 본 State 기준. 재추출 실패면 None)
    floor_satisfied: bool
    extraction_tokens: tuple[int, int]
    adopted_ids: tuple[str, ...] = ()   # Defender 지시 값으로 기록한 관찰 (adopt_instructed_value)
    residual_evidence: tuple[str, ...] = ()   # 지우라고 한 값의 근거 문구 중 고쳐 쓴 응답에 그대로 남은 것
    extraction_raw_output: str | None = None  # 고쳐 쓴 응답을 다시 추출한 LLM 출력 원문 (기록된 응답으로 다시 계산할 때 쓴다)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_no,
            "defense": self.defense.to_log_dict(),
            "rewrite": self.rewrite.to_log_dict() if self.rewrite else None,
            "k_projected": self.k_projected,
            "floor_satisfied": self.floor_satisfied,
            "adopted_ids": list(self.adopted_ids),
            "residual_evidence": list(self.residual_evidence),
            "extraction_raw_output": self.extraction_raw_output,
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
    defender_enabled / defender_mode / defender_name
    → Defender를 썼는지, 어떤 State를 보고 판정했는지, 어느 백엔드인지
    k_before / k_raw_projected / k_floor / k_released_projected
    → 실제 State 기준: 턴 시작 k / raw를 그대로 냈을 때 k (C0에서도 계산) / 하한 / 공개분 반영 시 k
    judged_k_floor
    → Defender가 실제로 지킨 하한. 무상태에서는 빈 State 기준이라 k_floor와 다를 수 있다
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
    defender_mode: DefenderMode
    defender_name: str
    k_before: int
    k_raw_projected: int
    k_floor: int
    k_released_projected: int
    judged_k_floor: int
    generalized: bool
    suppressed: bool
    rounds: tuple[DefenseRound, ...]

    @property
    def intervened(self) -> bool:
        return self.path in (ReleasePath.REWRITTEN, ReleasePath.REFUSAL)

    @property
    def floor_satisfied(self) -> bool:
        """실제 State 기준으로 하한이 지켜졌는지. 무상태 Defender에서는 거짓이 될 수 있다."""
        return floor_satisfied(self.k_before, self.k_released_projected, self.k_floor)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "released_response": self.released_response,
            "released_observations": [obs.to_log_dict() for obs in self.released_observations],
            "path": self.path.value,
            "refusal_reason": self.refusal_reason.value if self.refusal_reason else None,
            "defender_enabled": self.defender_enabled,
            "defender_mode": self.defender_mode.value if self.defender_enabled else None,
            "defender_name": self.defender_name if self.defender_enabled else None,
            "k_before": self.k_before,
            "k_raw_projected": self.k_raw_projected,
            "k_floor": self.k_floor,
            "k_released_projected": self.k_released_projected,
            "judged_k_floor": self.judged_k_floor,
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
        normalizer: ObservationNormalizer,
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
        session: RiskBackend,
        *,
        query: str,
        raw_response: str,
        raw_observations: Sequence[RawObservation] | None,
        turn: int,
        defender_mode: DefenderMode = DefenderMode.STATEFUL,
    ) -> ReleaseDecision:
        """
        input : condition, session (이 세션의 위험 계산 백엔드), query, raw_response,
                raw_observations (raw candidate 추출 결과. 추출에 실패했으면 None), turn,
                defender_mode (C1에서 Defender가 볼 State)
        output : ReleaseDecision
        """
        if condition not in SUPPORTED_CONDITIONS:
            raise ValueError(f"{condition.value}는 아직 지원하지 않습니다 (경량 언러닝 파트 이후).")

        defended = condition is Condition.C1
        # 판정에 쓰는 세션. 무상태면 같은 기준 집단의 빈 세션이다 (매 턴 새로 만든다)
        judge = session.fresh() if defended and defender_mode is DefenderMode.STATELESS else session

        k_before = session.k_current
        k_floor = min(session.config.k_req, k_before)
        judge_before = judge.k_current
        judge_floor = min(judge.config.k_req, judge_before)

        extraction_ok = raw_observations is not None
        raw_normalized = self._normalizer.normalize_all(raw_observations or ())
        k_raw = session.project(raw_normalized).k_after

        def decide(path: ReleasePath, text: str, observations: Sequence[NormalizedObservation], *,
                   reason: RefusalReason | None = None, rounds: Sequence[DefenseRound] = ()) -> ReleaseDecision:
            return ReleaseDecision(
                released_response=text,
                released_observations=tuple(observations),
                path=path,
                refusal_reason=reason,
                defender_enabled=defended,
                defender_mode=defender_mode,
                defender_name=session.backend_name,
                k_before=k_before,
                k_raw_projected=k_raw,
                k_floor=k_floor,
                k_released_projected=session.project(observations).k_after,
                judged_k_floor=judge_floor,
                generalized=path is ReleasePath.REWRITTEN and any(r.defense.generalized for r in rounds),
                suppressed=path is ReleasePath.REFUSAL
                or (path is ReleasePath.REWRITTEN and any(r.defense.suppressed_ids for r in rounds)),
                rounds=tuple(rounds),
            )

        def refuse(reason: RefusalReason, rounds: Sequence[DefenseRound] = ()) -> ReleaseDecision:
            return decide(ReleasePath.REFUSAL, self._config.refusal_text, (), reason=reason, rounds=rounds)

        # ── C0: 검사 없이 공개 ──
        if not defended:
            return decide(ReleasePath.PASSTHROUGH, raw_response, raw_normalized)

        # ── C1 ──
        if not extraction_ok:
            return refuse(RefusalReason.RAW_EXTRACTION_FAILED)   # 위험을 계산할 수 없으면 내보내지 않는다

        text = raw_response
        observations: Sequence[RawObservation] = raw_observations or ()
        normalized = raw_normalized
        rounds: list[DefenseRound] = []

        carry: list[RewriteInstruction] = []      # 앞 회차에서 지우라고 했는데 근거 문구가 남은 항목
        for round_no in range(1, self._config.max_defense_rounds + 1):
            defense = judge.defend(normalized)
            if not defense.intervened and not carry:
                break

            instructions = (build_instructions(defense, observations) if defense.intervened else []) + carry
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
            normalized, adopted = self._adopt_instructed(
                self._normalizer.normalize_all(observations), instructions, text, turn)
            k_judged = judge.project(normalized).k_after
            # 재추출이 놓쳐도, 지우라고 한 값의 근거 문구가 글자 그대로 남아 있으면 통과시키지 않는다
            carry = residual_instructions(instructions, text) if self._config.check_residual_evidence else []
            residual = tuple(span for item in carry for span in item.evidence)
            satisfied = floor_satisfied(judge_before, k_judged, judge_floor) and not residual
            rounds.append(DefenseRound(round_no, defense, rewrite, k_judged, satisfied,
                                       (extraction.input_tokens, extraction.output_tokens), adopted, residual,
                                       extraction.raw_output))
            if satisfied:
                return decide(ReleasePath.REWRITTEN, text, normalized, rounds=rounds)
        else:
            return refuse(RefusalReason.ROUNDS_EXHAUSTED, rounds)

        # Defender가 더 고칠 것이 없다고 한 경우: 하한을 직접 확인하고 공개한다
        if not floor_satisfied(judge_before, judge.project(normalized).k_after, judge_floor):
            return refuse(RefusalReason.FLOOR_NOT_MET, rounds)
        path = ReleasePath.REWRITTEN if rounds else ReleasePath.ALLOWED
        return decide(path, text, normalized, rounds=rounds)

    def _adopt_instructed(
        self,
        normalized: tuple[NormalizedObservation, ...],
        instructions: Sequence[RewriteInstruction],
        text: str,
        turn: int,
    ) -> tuple[tuple[NormalizedObservation, ...], tuple[str, ...]]:
        """
        input : normalized (고쳐 쓴 응답에서 다시 뽑아 정규화한 관찰), instructions (그 고쳐 쓰기의 지시),
                text (고쳐 쓴 응답), turn
        output : (보정된 관찰, 보정한 observation_id)

        일반화를 지시한 QI가 고쳐 쓴 응답에 실렸는데 정규화된(MAPPED) 관찰이 없으면, Defender가 지시한 값으로 기록한다.
          (가) 그 QI의 관찰이 추출됐지만 표현을 규칙으로 읽지 못한 경우 → 첫 관찰을 지시 값으로 바꾼다
          (나) 추출기가 그 QI를 놓쳤지만 지시한 표현이 응답에 글자 그대로 들어 있는 경우 → 관찰을 하나 만든다
        이유: "IT 분야 전문직"처럼 풀어 쓴 일반화 표현은 규칙으로 읽지 못할 수 있다.
              그대로 두면 공격자는 그 정보를 봤는데 State에는 없는 상태가 된다.
        MAPPED 관찰이 하나라도 있으면 추출 결과를 그대로 믿는다 (더 구체적인 값이 새어 나왔을 수 있으므로).
        """
        if not self._config.adopt_instructed_value:
            return normalized, ()

        result = list(normalized)
        adopted: list[str] = []
        seen: set[str] = set()
        for item in instructions:
            if item.action is not RewriteAction.GENERALIZE or not item.replacement or item.attribute in seen:
                continue
            seen.add(item.attribute)
            same = [i for i, obs in enumerate(result) if obs.attribute == item.attribute]
            if any(result[i].is_mapped for i in same):
                continue
            if same:
                first = same[0]
                result[first] = replace(result[first], status=MappingStatus.MAPPED,
                                        reason=MappingReason.DEFENDER_INSTRUCTED,
                                        canonical_value=item.replacement, candidates=())
                adopted.append(result[first].observation_id)
                continue
            phrase = next((part for part in _phrase_parts(item.phrase) if part.lower() in text.lower()), None)
            if phrase is not None:
                observation_id = f"t{turn}-adopt-{item.attribute}"
                result.append(NormalizedObservation(
                    observation_id=observation_id, attribute=item.attribute, raw_value=phrase, value_en=None,
                    confidence=5, source_turn=turn, status=MappingStatus.MAPPED,
                    reason=MappingReason.DEFENDER_INSTRUCTED, canonical_value=item.replacement,
                ))
                adopted.append(observation_id)
        return tuple(result), tuple(adopted)


def _phrase_parts(phrase: str) -> list[str]:
    """'대학 이상의 학력 (higher education)' → ['대학 이상의 학력', 'higher education']."""
    head, _, tail = phrase.partition(" (")
    return [part for part in (head.strip(), tail.rstrip(")").strip()) if len(part) >= 2]


def residual_instructions(instructions: Sequence[RewriteInstruction], text: str) -> list[RewriteInstruction]:
    """
    input : instructions (방금 고쳐 쓰기에 쓴 지시), text (고쳐 쓴 응답)
    output : 삭제 지시 가운데 근거 문구가 응답에 그대로 남은 항목 (남은 문구만 근거로 담아 다음 회차에 다시 지시한다)

    10/06 실행: 출생지를 지우라는 지시 뒤에도 "Midwest modesty versus Swiss efficiency" 인용문이 남았고,
    재추출은 이를 출생지로 뽑지 않아 그대로 공개됐다. 추출기가 놓치는 경우를 글자 대조로 한 번 더 막는다.
    """
    remaining: list[RewriteInstruction] = []
    for item in instructions:
        if item.action is not RewriteAction.SUPPRESS:
            continue
        left = tuple(span for span in item.evidence
                     if len(span.strip()) >= MIN_RESIDUAL_SPAN_CHARS and locate_span(span, text) is not None)
        if left:
            remaining.append(replace(item, evidence=left))
    return remaining


def build_instructions(defense: DefenseOutcome, observations: Sequence[RawObservation]) -> list[RewriteInstruction]:
    """
    input : defense (Defender 판정), observations (그 판정의 대상이 된 추출 관찰)
    output : Rewrite LLM에 줄 수정 지시 (추출 순서 유지)

    Defender가 정한 값만 옮긴다. 일반화 수준을 여기서 다시 고르지 않는다.
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
            replacement_text=defense.replacement_text.get(obs.observation_id),
        ))
    return instructions
