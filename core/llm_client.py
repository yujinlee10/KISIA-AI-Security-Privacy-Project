# monogpt(Anthropic Messages API 호환 프록시) 호출을 담당하는 공통 클라이언트
#
# fuzzer/llm_client.py(한재이 파트)와 같은 호출 계약을 따른다.
#   POST {LLM_API_BASE}/v1/messages
#   headers : x-api-key, anthropic-version, Content-Type
#   payload : model, max_tokens, system, messages
#   응답    : content[*].text
#
# fuzzer 버전과 달라진 점 (호출 계약은 그대로, 운영 안정성만 보강)
#   1) import 시 .env 로딩·print 부작용 없음 → Settings를 주입받는다.
#   2) 오류를 빈 문자열이나 '응답 없음'으로 숨기지 않고 종류별 예외로 올린다.
#      (D1 Stage 2: 파싱 실패를 'QI 없음' 정상 결과와 구분해야 한다)
#   3) 429·5xx·timeout은 지수 백오프로 재시도, 401·400 같은 영구 오류는 즉시 실패.
#   4) requests.Session을 재사용해 매 호출마다 TCP/TLS 연결을 새로 맺지 않는다.
#   5) stop_reason을 함께 반환해 max_tokens로 잘린 응답을 호출 측이 알 수 있다.
#   6) 어떤 예외 메시지에도 API 키·요청 헤더를 넣지 않는다.

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from core.config import ANTHROPIC_VERSION, Settings, get_settings


# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────

MESSAGES_PATH = "/v1/messages"

# 재시도 대상 HTTP 상태: 429(rate limit), 5xx, 529(Anthropic overloaded)
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})

# 지수 백오프: 1초, 2초, 4초 ... 최대 30초
BACKOFF_BASE_SEC = 1.0
BACKOFF_CAP_SEC = 30.0

# 오류 메시지에 담을 응답 본문 최대 길이 (긴 HTML 오류 페이지 등이 로그를 덮지 않도록)
ERROR_BODY_PREVIEW_CHARS = 300


# ──────────────────────────────────────────────────────────────
# 예외 계층
#   LLMError
#    ├── LLMTransportError       : 네트워크·timeout (재시도 대상)
#    ├── LLMHTTPError            : 비정상 HTTP 상태
#    │    └── LLMRateLimitError  : 429
#    └── LLMResponseFormatError  : 200이지만 응답 구조가 계약과 다름
# ──────────────────────────────────────────────────────────────

class LLMError(RuntimeError):
    """LLM 호출 실패의 공통 상위 예외. attempts는 실제로 보낸 요청 수."""

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        super().__init__(message)
        self.attempts = attempts


class LLMTransportError(LLMError):
    """연결 실패·timeout 등 HTTP 응답 자체를 받지 못한 경우."""


class LLMHTTPError(LLMError):
    """HTTP 상태가 200이 아닌 경우. status_code로 원인 구분."""

    def __init__(self, message: str, *, status_code: int, attempts: int = 0) -> None:
        super().__init__(message, attempts=attempts)
        self.status_code = status_code


class LLMRateLimitError(LLMHTTPError):
    """429. 재시도를 모두 소진해도 풀리지 않으면 올라온다."""


class LLMResponseFormatError(LLMError):
    """200 응답이지만 JSON이 아니거나, text 블록이 없거나 비어 있는 경우."""


# ──────────────────────────────────────────────────────────────
# 응답 객체
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMResponse:
    """
    text
    → 응답 본문 (content의 text 블록을 순서대로 이어붙인 것)
    model
    → 실제로 응답한 모델 ID (프록시가 다른 모델로 라우팅했는지 확인용)
    stop_reason
    → 생성 종료 사유. 'max_tokens'면 응답이 잘린 것이다.
    input_tokens / output_tokens
    → 토큰 사용량 (응답에 없으면 None)
    latency_ms
    → 성공한 마지막 요청의 왕복 시간
    attempts
    → 성공까지 보낸 요청 수 (1이면 재시도 없음)
    """
    text: str
    model: str | None
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    attempts: int

    @property
    def truncated(self) -> bool:
        """max_tokens에 걸려 잘린 응답인지. QI 추출 전에 확인해야 한다."""
        return self.stop_reason == "max_tokens"


# ──────────────────────────────────────────────────────────────
# 클라이언트
# ──────────────────────────────────────────────────────────────

class LLMClient:
    """
    monogpt 호출 클라이언트. 하나의 인스턴스가 하나의 HTTP 세션을 재사용한다.

    역할(Target 응답 생성 / QI 추출 / Mutation)마다 system_prompt와 max_tokens가 다르므로,
    그 값은 생성자가 아니라 complete() 호출 시에 넘긴다. 같은 클라이언트를 여러 역할이 공유해도 된다.

    session · sleep · clock은 테스트에서 가짜로 바꿔 끼우기 위한 주입 지점이다.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._url = f"{settings.llm_api_base}{MESSAGES_PATH}"
        self._last_request_at: float | None = None

    @property
    def model(self) -> str:
        """이 클라이언트가 요청하는 모델 ID (로그 기록용)."""
        return self._settings.llm_model

    # ── 공개 메서드 ─────────────────────────────────────────

    def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """
        input : user_prompt (사용자 메시지), system_prompt (역할별 지시문, 선택),
                max_tokens (None이면 설정값), temperature (None이면 보내지 않음 → 서버 기본값)
        output : LLMResponse

        한 번의 단일 턴 요청을 보낸다. 실패하면 예외를 올리며, 빈 문자열을 반환하지 않는다.

        temperature를 기본값으로 정하지 않는 이유:
          Extractor는 고정값, Mutation은 다양성이 필요한 등 역할마다 연구 설정이 다르고,
          D1에서 Pilot 후 freeze할 값이다. 호출하는 쪽이 명시적으로 정하고 기록하게 한다.
        """
        if not user_prompt or not user_prompt.strip():
            raise ValueError("user_prompt는 비어 있을 수 없습니다.")

        payload = self._build_payload(user_prompt, system_prompt, max_tokens, temperature)
        return self._send_with_retry(payload)

    def close(self) -> None:
        """HTTP 세션을 닫는다. with 문으로 쓰면 자동 호출된다."""
        self._session.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── 요청 구성 ──────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        # 이 dict는 요청에만 쓰고 어떤 로그·예외에도 넣지 않는다.
        return {
            "x-api-key": self._settings.llm_api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        user_prompt: str,
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "max_tokens": max_tokens if max_tokens is not None else self._settings.llm_max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    # ── 전송 · 재시도 ──────────────────────────────────────

    def _send_with_retry(self, payload: dict[str, Any]) -> LLMResponse:
        max_attempts = self._settings.llm_max_retries + 1
        last_error: LLMError | None = None

        for attempt in range(1, max_attempts + 1):
            self._throttle()
            started = self._clock()

            # 1) 네트워크 단계: 응답 자체를 못 받은 경우
            try:
                response = self._session.post(
                    self._url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self._settings.llm_timeout_sec,
                )
            except requests.Timeout:
                last_error = LLMTransportError(
                    f"요청 timeout ({self._settings.llm_timeout_sec}초)", attempts=attempt
                )
                self._backoff(attempt, max_attempts, retry_after=None)
                continue
            except requests.ConnectionError as exc:
                last_error = LLMTransportError(
                    f"연결 실패: {type(exc).__name__}", attempts=attempt
                )
                self._backoff(attempt, max_attempts, retry_after=None)
                continue

            latency_ms = (self._clock() - started) * 1000.0

            # 2) 정상 응답
            if response.status_code == 200:
                return self._parse(response, latency_ms=latency_ms, attempts=attempt)

            # 3) 재시도 가능한 HTTP 오류
            if response.status_code in RETRYABLE_STATUS:
                error_cls = LLMRateLimitError if response.status_code == 429 else LLMHTTPError
                last_error = error_cls(
                    _http_error_message(response),
                    status_code=response.status_code,
                    attempts=attempt,
                )
                self._backoff(attempt, max_attempts, retry_after=_retry_after_sec(response))
                continue

            # 4) 영구 오류 (400 잘못된 요청, 401 인증, 403 권한, 404 경로 등): 재시도해도 같으므로 즉시 실패
            raise LLMHTTPError(
                _http_error_message(response),
                status_code=response.status_code,
                attempts=attempt,
            )

        # 재시도 소진
        assert last_error is not None
        raise last_error

    def _throttle(self) -> None:
        """직전 요청 이후 llm_min_interval_sec가 지나지 않았으면 남은 시간만큼 기다린다."""
        interval = self._settings.llm_min_interval_sec
        now = self._clock()
        if interval > 0 and self._last_request_at is not None:
            wait = interval - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_request_at = now

    def _backoff(self, attempt: int, max_attempts: int, *, retry_after: float | None) -> None:
        """마지막 시도가 아니면 다음 시도 전에 대기한다. 서버가 Retry-After를 주면 그 값을 우선한다."""
        if attempt >= max_attempts:
            return
        if retry_after is not None:
            delay = min(retry_after, BACKOFF_CAP_SEC)
        else:
            delay = min(BACKOFF_BASE_SEC * (2 ** (attempt - 1)), BACKOFF_CAP_SEC)
        self._sleep(delay)

    # ── 응답 파싱 ──────────────────────────────────────────

    def _parse(self, response: requests.Response, *, latency_ms: float, attempts: int) -> LLMResponse:
        """
        input : 200 응답
        output : LLMResponse

        fuzzer는 content[0]["text"]만 읽지만, 응답에 text 외 블록이 섞이거나 text가 여러 개로
        나뉠 수 있어 type == "text"인 블록을 모두 이어붙인다.
        text가 하나도 없거나 비어 있으면 정상 응답으로 취급하지 않는다.
        """
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            raise LLMResponseFormatError(
                "응답이 JSON 형식이 아닙니다.", attempts=attempts
            ) from None

        if not isinstance(body, dict):
            raise LLMResponseFormatError("응답 최상위가 객체가 아닙니다.", attempts=attempts)

        content = body.get("content")
        if not isinstance(content, list):
            raise LLMResponseFormatError("응답에 content 배열이 없습니다.", attempts=attempts)

        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "".join(texts)
        if not text.strip():
            raise LLMResponseFormatError(
                f"응답에 text 내용이 없습니다 (stop_reason={body.get('stop_reason')}).",
                attempts=attempts,
            )

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return LLMResponse(
            text=text,
            model=body.get("model"),
            stop_reason=body.get("stop_reason"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=latency_ms,
            attempts=attempts,
        )


# ──────────────────────────────────────────────────────────────
# 편의 함수
# ──────────────────────────────────────────────────────────────

def build_client() -> LLMClient:
    """
    input : 없음
    output : LLMClient

    프로젝트 루트 .env 기준 설정으로 클라이언트를 만든다. 실행 스크립트에서 쓰는 진입점.
    """
    return LLMClient(get_settings())


def _http_error_message(response: requests.Response) -> str:
    """HTTP 오류 메시지. 상태 코드와 응답 본문 앞부분만 담는다 (요청 헤더는 넣지 않음)."""
    preview = (response.text or "")[:ERROR_BODY_PREVIEW_CHARS]
    return f"HTTP {response.status_code}: {preview}"


def _retry_after_sec(response: requests.Response) -> float | None:
    """Retry-After 헤더를 초 단위로 읽는다. 없거나 숫자가 아니면 None."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


# ──────────────────────────────────────────────────────────────
# 연결 확인용 실행: python -m core.llm_client
# 실제 monogpt에 아주 짧은 요청 1건을 보낸다. 키는 출력하지 않는다.
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    settings = get_settings()
    print("------")
    print("설정:", settings.describe())

    with LLMClient(settings) as client:
        result = client.complete("연결 확인입니다. '확인'이라고 한 단어로만 답하세요.", max_tokens=20)

    print("------")
    print("응답 모델   :", result.model)
    print("stop_reason :", result.stop_reason)
    print("토큰(in/out):", result.input_tokens, "/", result.output_tokens)
    print("지연(ms)    :", round(result.latency_ms, 1))
    print("시도 횟수   :", result.attempts)
    print("응답        :", result.text.strip()[:50])
    print("------")
