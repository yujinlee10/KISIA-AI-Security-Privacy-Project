# QI 추출 결과를 담는 데이터 구조
#
# 필드 이름은 D1 Stage 2 개발 스펙을 따른다.
#   attribute, raw_value, inference_mode, confidence, evidence_spans, source_turn
# 여기에 두 가지를 더했다.
#   observation_id : D1 Stage 4가 요구하는 불변 ID (State History에서 추적)
#   value_en       : 정규화 보조 힌트. 응답은 한국어, 기준 집단(P_ref) 값은 영어라
#                    Normalizer가 대조할 영어 표현을 같은 LLM 호출에서 함께 받아둔다.
#                    canonical node가 아니며, raw_value가 원본이다.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.schemas import InferenceMode


class RejectReason(str, Enum):
    """관찰이 거부된 이유. 로그에서 추출 품질을 분석할 때 쓴다."""
    INVALID_ATTRIBUTE = "INVALID_ATTRIBUTE"              # 8개 QI 코드가 아님
    MISSING_VALUE = "MISSING_VALUE"                      # raw_value 없음
    INVALID_INFERENCE_MODE = "INVALID_INFERENCE_MODE"    # EXPLICIT/CONTEXTUAL이 아님
    INVALID_CONFIDENCE = "INVALID_CONFIDENCE"            # 1~5 정수가 아님
    MISSING_EVIDENCE = "MISSING_EVIDENCE"                # 근거 문구가 없음
    EVIDENCE_NOT_IN_RESPONSE = "EVIDENCE_NOT_IN_RESPONSE"  # 근거가 응답 원문에 없음 (지어낸 근거)
    QUERY_ONLY = "QUERY_ONLY"                            # 근거가 전부 질문 문구의 반복


@dataclass(frozen=True)
class EvidenceSpan:
    """
    text
    → 응답 원문에서 실제로 찾은 구간 (LLM이 준 문자열이 아니라 원문에서 잘라낸 문자열)
    start / end
    → 응답 원문 안의 위치 [start, end)
    """
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class RawObservation:
    """
    검증을 통과한 정규화 전 관찰값 하나.

    observation_id
    → "t{턴}-o{순번}" 형식의 불변 ID
    attribute
    → 8개 QI 코드 중 하나
    raw_value
    → 응답에 나온 표현 그대로 (예: "취리히", "30대 중반")
    value_en
    → 영어 힌트 (예: "Zurich"). 없을 수 있음
    inference_mode
    → EXPLICIT / CONTEXTUAL
    confidence
    → 1~5 정수
    evidence_spans
    → 응답 원문에서 확인된 근거 구간들
    source_turn
    → 이 관찰이 나온 턴 번호
    """
    observation_id: str
    attribute: str
    raw_value: str
    value_en: str | None
    inference_mode: InferenceMode
    confidence: int
    evidence_spans: tuple[EvidenceSpan, ...]
    source_turn: int
    unverified_spans: tuple[str, ...] = ()   # LLM이 근거로 냈지만 응답 원문에서 찾지 못해 뺀 문구

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "attribute": self.attribute,
            "raw_value": self.raw_value,
            "value_en": self.value_en,
            "inference_mode": self.inference_mode.value,
            "confidence": self.confidence,
            "evidence_spans": [span.text for span in self.evidence_spans],
            "unverified_spans": list(self.unverified_spans),
            "source_turn": self.source_turn,
        }


@dataclass(frozen=True)
class RejectedObservation:
    """
    검증에서 떨어진 관찰. 버리지 않고 이유와 함께 남긴다.
    "근거 없는 관찰"과 "QI가 없었던 턴"을 로그에서 구분하기 위함이다 (D1 Stage 2).

    raw_item
    → LLM이 준 원래 항목 (분석용)
    reason
    → 거부 이유
    detail
    → 사람이 읽을 보충 설명
    """
    raw_item: dict[str, Any]
    reason: RejectReason
    detail: str

    def to_log_dict(self) -> dict[str, Any]:
        return {"raw_item": self.raw_item, "reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True)
class ExtractionResult:
    """
    한 턴의 추출 결과.

    observations
    → 검증 통과 관찰. 비어 있으면 "이 응답에서 QI를 찾지 못함"이다 (파싱 실패는 예외로 따로 올라옴)
    rejected
    → 거부된 관찰
    source_turn
    → 턴 번호
    response_text
    → 근거 위치(start/end)의 기준이 된 응답 원문 (NFC 정규화 후)
    response_truncated
    → 입력 응답이 max_tokens로 잘린 것이었는지 (잘린 응답은 단서가 덜 나왔을 수 있다)
    prompt_version / requested_model / response_model / temperature
    → 재현용 설정 기록
    attempts
    → 파싱 성공까지 LLM 호출 횟수
    input_tokens / output_tokens / latency_ms
    → 이 턴 추출에 든 비용 합계 (D1 Stage 15: Extractor 호출을 따로 집계)
    raw_output
    → 마지막 LLM 출력 원문 (trusted 로그 전용, 추출 오류 분석용)
    """
    observations: tuple[RawObservation, ...]
    rejected: tuple[RejectedObservation, ...]
    source_turn: int
    response_text: str
    response_truncated: bool
    prompt_version: str
    requested_model: str | None
    response_model: str | None
    temperature: float | None
    attempts: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    raw_output: str

    @property
    def attributes(self) -> tuple[str, ...]:
        """통과한 관찰의 QI 코드 (등장 순서, 중복 제거)."""
        return tuple(dict.fromkeys(obs.attribute for obs in self.observations))

    def to_log_dict(self) -> dict[str, Any]:
        """trusted 로그용. Fuzzer나 Mutation LLM으로 넘기지 않는다."""
        return {
            "source_turn": self.source_turn,
            "observations": [obs.to_log_dict() for obs in self.observations],
            "rejected": [rej.to_log_dict() for rej in self.rejected],
            "response_truncated": self.response_truncated,
            "prompt_version": self.prompt_version,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "temperature": self.temperature,
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "raw_output": self.raw_output,
        }
