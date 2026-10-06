# LLM 기반 QI 추출기: (query, response) 한 쌍 → 검증된 RawObservation 목록
#
# D1 Stage 2 개발 기준
#   - 입력은 현재 턴의 (q_t, r_t) 한 쌍뿐이다. 과거 턴은 다시 추출하지 않는다.
#   - LLM structured JSON. attribute는 8개 enum, confidence는 1~5 정수로 엄격 검증한다.
#   - evidence_spans는 응답 원문의 구간이어야 한다. 근거 없는 관찰은 정상 관찰로 넘기지 않는다.
#   - 질문에만 있는 값은 공개로 보지 않는다 (query-only guard).
#   - prompt 버전·모델·temperature를 고정하고 기록한다.
#
# 오류를 세 층으로 나눈다 (오류를 "QI 없음"으로 숨기지 않기 위함)
#   LLM 호출 실패            → core.llm_client.LLMError 그대로 전파
#   출력 파싱 실패(재시도 후) → ExtractionError
#   항목 단위 검증 실패       → 결과의 rejected에 이유와 함께 기록, 통과 항목은 그대로 사용
#   따라서 ExtractionResult.observations가 비어 있다면 "QI를 못 찾은 정상 턴"만 뜻한다.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from core.llm_client import LLMResponse
from core.schemas import CONFIDENCE_MAX, CONFIDENCE_MIN, QI_TYPE_SET, InferenceMode
from qi_extractor.evidence import is_query_echo, locate_span, normalize_text
from qi_extractor.schema import (
    EvidenceSpan,
    ExtractionResult,
    RawObservation,
    RejectedObservation,
    RejectReason,
)


BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

# v1: value_en을 "영어로 옮긴 짧은 표현"으로만 요구. v2: QI별 표준 영어 형식을 요구 (정규화 실패를 줄이기 위함).
# 2026-10-05 실제 실행에서 "coding job", "Small quiet town"처럼 표에 대조할 수 없는 value_en이 나와 v2를 만들었다.
DEFAULT_PROMPT_VERSION = "extractor_system_v2"

QUESTION_SECTION_HEADER = "[질문]"
RESPONSE_SECTION_HEADER = "[응답]"


# ──────────────────────────────────────────────────────────────
# 예외 · 설정
# ──────────────────────────────────────────────────────────────

class ExtractionError(RuntimeError):
    """재시도 후에도 추출기 출력을 해석하지 못했을 때. 이 턴은 '추출 실패'로 기록해야 한다."""

    def __init__(self, message: str, *, attempts: int, raw_output: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.raw_output = raw_output


class OutputParseError(ValueError):
    """LLM 출력이 약속한 JSON 모양이 아닐 때 (내부용, 재시도 판단에 씀)."""


class CompletionClient(Protocol):
    """core.llm_client.LLMClient가 이 모양을 만족한다."""

    def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        ...


@dataclass(frozen=True)
class ExtractorConfig:
    """
    prompt_version
    → prompts/ 안의 system prompt 파일 이름
    max_tokens
    → None이면 core 설정값(기본 1200). QI 8개 JSON이면 충분하다
    temperature
    → 0.0. D1이 Extractor temperature 고정을 요구한다. 값 자체는 Pilot에서 재확인한다
    max_parse_attempts
    → 출력 파싱 실패 시 같은 입력으로 다시 호출하는 최대 횟수 (첫 호출 포함)
    """
    prompt_version: str = DEFAULT_PROMPT_VERSION
    max_tokens: int | None = None
    temperature: float | None = 0.0
    max_parse_attempts: int = 2


# ──────────────────────────────────────────────────────────────
# 추출기
# ──────────────────────────────────────────────────────────────

class QIExtractor:
    """
    LLM client를 주입받아 한 턴씩 추출한다. Target과 같은 client 인스턴스를 공유해도 된다
    (역할별 system prompt·temperature는 호출마다 따로 넘기므로 섞이지 않는다).
    """

    def __init__(
        self,
        client: CompletionClient,
        config: ExtractorConfig = ExtractorConfig(),
        *,
        requested_model: str | None = None,
    ) -> None:
        if config.max_parse_attempts < 1:
            raise ValueError("max_parse_attempts는 1 이상이어야 합니다.")
        self._client = client
        self._config = config
        self._requested_model = requested_model or getattr(client, "model", None)
        self._system_prompt = load_prompt(config.prompt_version)

    def extract(
        self,
        query: str,
        response: str,
        *,
        source_turn: int,
        response_truncated: bool = False,
    ) -> ExtractionResult:
        """
        input : query (이번 턴 질문 원문), response (공개된 응답 원문),
                source_turn (턴 번호), response_truncated (응답이 max_tokens로 잘렸는지)
        output : ExtractionResult

        어떤 응답을 넣을지는 호출하는 쪽 책임이다.
        State에 반영할 관찰을 뽑을 때는 반드시 released_response를 넣는다 (raw candidate 금지).
        Defender가 공개 전 위험을 계산할 때는 raw candidate를 넣어 projected 관찰을 뽑는다.
        """
        query_text = normalize_text(query or "")
        response_text = normalize_text(response or "")
        if not response_text.strip():
            raise ValueError("response는 비어 있을 수 없습니다.")

        user_prompt = build_user_prompt(query_text, response_text)

        items: list[Any] | None = None
        last_output = ""
        last_problem = ""
        total_in = total_out = 0
        total_latency = 0.0
        response_model: str | None = None
        attempts = 0

        for attempts in range(1, self._config.max_parse_attempts + 1):
            llm = self._client.complete(
                user_prompt,
                system_prompt=self._system_prompt,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )
            last_output = llm.text
            response_model = llm.model
            total_in += llm.input_tokens or 0
            total_out += llm.output_tokens or 0
            total_latency += llm.latency_ms

            if llm.truncated:
                # JSON이 중간에 끊겼을 가능성이 크다. 잘린 JSON 일부만 쓰면 관찰이 조용히 빠진다.
                last_problem = "추출기 출력이 max_tokens로 잘렸습니다."
                continue
            try:
                items = parse_output(llm.text)
                break
            except OutputParseError as exc:
                last_problem = str(exc)

        if items is None:
            raise ExtractionError(
                f"추출기 출력을 해석하지 못했습니다 ({attempts}회 시도): {last_problem}",
                attempts=attempts,
                raw_output=last_output,
            )

        observations, rejected = validate_items(items, query_text, response_text, source_turn)

        return ExtractionResult(
            observations=tuple(observations),
            rejected=tuple(rejected),
            source_turn=source_turn,
            response_text=response_text,
            response_truncated=response_truncated,
            prompt_version=self._config.prompt_version,
            requested_model=self._requested_model,
            response_model=response_model,
            temperature=self._config.temperature,
            attempts=attempts,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=total_latency,
            raw_output=last_output,
        )


# ──────────────────────────────────────────────────────────────
# prompt 조립
# ──────────────────────────────────────────────────────────────

def load_prompt(prompt_version: str) -> str:
    """
    input : prompt_version (prompts/ 안의 파일 이름, 확장자 제외)
    output : prompt 문자열
    """
    path = PROMPT_DIR / f"{prompt_version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"extractor prompt 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"extractor prompt 파일이 비어 있습니다: {path}")
    return text


def build_user_prompt(query: str, response: str) -> str:
    """질문은 해석 문맥으로만, 추출 대상은 응답이라는 구분이 보이도록 두 구역으로 나눈다."""
    return (
        f"{QUESTION_SECTION_HEADER}\n{query.strip()}\n\n"
        f"{RESPONSE_SECTION_HEADER}\n{response.strip()}"
    )


# ──────────────────────────────────────────────────────────────
# 출력 파싱
# ──────────────────────────────────────────────────────────────

def parse_output(text: str) -> list[Any]:
    """
    input : LLM 출력 원문
    output : observations 배열 (항목 검증 전)

    "JSON만 출력하라"고 해도 LLM이 ```json 코드블록이나 앞뒤 설명을 붙이는 경우가 있다.
    첫 '{'부터 마지막 '}'까지만 잘라 해석한다. 그래도 안 되면 OutputParseError.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise OutputParseError("출력에 JSON 객체가 없습니다.")

    try:
        body = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise OutputParseError(f"JSON 해석 실패: {exc.msg}") from None

    if not isinstance(body, dict) or "observations" not in body:
        raise OutputParseError("최상위에 observations 키가 없습니다.")
    observations = body["observations"]
    if not isinstance(observations, list):
        raise OutputParseError("observations가 배열이 아닙니다.")
    return observations


# ──────────────────────────────────────────────────────────────
# 항목 검증
# ──────────────────────────────────────────────────────────────

def validate_items(
    items: list[Any],
    query: str,
    response: str,
    source_turn: int,
) -> tuple[list[RawObservation], list[RejectedObservation]]:
    """
    input : items (파싱된 observations 배열), query·response (NFC 정규화됨), source_turn
    output : (통과 관찰 목록, 거부 관찰 목록)

    한 항목이 틀려도 나머지는 계속 검사한다. 거부 항목도 이유와 함께 돌려준다.
    observation_id의 순번은 통과한 항목에만 매긴다.
    """
    accepted: list[RawObservation] = []
    rejected: list[RejectedObservation] = []

    for item in items:
        outcome = _validate_item(item, query, response, source_turn, len(accepted) + 1)
        if isinstance(outcome, RawObservation):
            accepted.append(outcome)
        else:
            rejected.append(outcome)

    return accepted, rejected


def _validate_item(
    item: Any,
    query: str,
    response: str,
    source_turn: int,
    sequence: int,
) -> RawObservation | RejectedObservation:
    if not isinstance(item, dict):
        return RejectedObservation({"value": repr(item)}, RejectReason.MISSING_VALUE, "항목이 객체가 아닙니다.")

    def reject(reason: RejectReason, detail: str) -> RejectedObservation:
        return RejectedObservation(dict(item), reason, detail)

    # attribute: 8개 코드만. 대소문자·공백 차이는 허용한다 ("loc " → "LOC")
    attribute = item.get("attribute")
    attribute = attribute.strip().upper() if isinstance(attribute, str) else None
    if attribute not in QI_TYPE_SET:
        return reject(RejectReason.INVALID_ATTRIBUTE, f"attribute={item.get('attribute')!r}")

    raw_value = item.get("raw_value")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return reject(RejectReason.MISSING_VALUE, "raw_value가 비어 있습니다.")

    mode_raw = item.get("inference_mode")
    try:
        inference_mode = InferenceMode(mode_raw.strip().upper() if isinstance(mode_raw, str) else mode_raw)
    except ValueError:
        return reject(RejectReason.INVALID_INFERENCE_MODE, f"inference_mode={mode_raw!r}")

    # confidence: 정수만. True/False(파이썬에서 int의 하위형)와 3.0 같은 실수, "3" 같은 문자열은 거부한다.
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int) \
            or not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
        return reject(RejectReason.INVALID_CONFIDENCE, f"confidence={confidence!r}")

    spans_raw = item.get("evidence_spans")
    if not isinstance(spans_raw, list) or not spans_raw \
            or not all(isinstance(s, str) and s.strip() for s in spans_raw):
        return reject(RejectReason.MISSING_EVIDENCE, "evidence_spans가 비어 있거나 문자열 배열이 아닙니다.")

    # 근거가 하나라도 응답에 없으면 관찰 전체를 거부한다. 일부라도 지어낸 근거면 값도 신뢰할 수 없다.
    spans: list[EvidenceSpan] = []
    for span_text in spans_raw:
        located = locate_span(span_text, response)
        if located is None:
            return reject(RejectReason.EVIDENCE_NOT_IN_RESPONSE, f"응답에 없는 근거: {span_text[:60]!r}")
        start, end = located
        spans.append(EvidenceSpan(text=response[start:end], start=start, end=end))

    if is_query_echo([span.text for span in spans], query):
        return reject(RejectReason.QUERY_ONLY, "근거가 모두 질문 문구의 반복입니다.")

    value_en = item.get("value_en")
    value_en = value_en.strip() if isinstance(value_en, str) and value_en.strip() else None

    return RawObservation(
        observation_id=f"t{source_turn}-o{sequence}",
        attribute=attribute,
        raw_value=raw_value.strip(),
        value_en=value_en,
        inference_mode=inference_mode,
        confidence=confidence,
        evidence_spans=tuple(spans),
        source_turn=source_turn,
    )
