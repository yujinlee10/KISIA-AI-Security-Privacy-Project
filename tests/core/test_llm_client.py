# core/llm_client.py 단위 테스트
# 실제 네트워크 호출 없이 가짜 Session으로 정상·재시도·오류·비밀값 비노출을 검증한다.

from __future__ import annotations

from typing import Any

import pytest
import requests

from core.config import Settings
from core.llm_client import (
    LLMClient,
    LLMHTTPError,
    LLMRateLimitError,
    LLMResponseFormatError,
    LLMTransportError,
)

FAKE_KEY = "sk-test-SECRET-should-never-leak"


# ──────────────────────────────────────────────────────────────
# 테스트용 가짜 객체
# ──────────────────────────────────────────────────────────────

class FakeResponse:
    """requests.Response에서 클라이언트가 쓰는 속성만 흉내 낸다."""

    def __init__(self, status_code: int, body: Any = None, *, text: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else ("" if body is None else str(body))
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """미리 넣어둔 응답(또는 예외)을 순서대로 돌려주고, 받은 요청을 기록한다."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


def ok_body(text: str = "응답입니다", stop_reason: str = "end_turn") -> dict:
    return {
        "model": "test-model",
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def make_settings(**overrides: Any) -> Settings:
    base = {
        "LLM_API_KEY": FAKE_KEY,
        "LLM_API_BASE": "https://monogpt.example.com",
        "LLM_MODEL": "test-model",
        "LLM_MAX_RETRIES": "2",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(base)


def make_client(outcomes: list[Any], **settings_overrides: Any):
    session = FakeSession(outcomes)
    sleeps: list[float] = []
    client = LLMClient(
        make_settings(**settings_overrides),
        session=session,            # type: ignore[arg-type]
        sleep=sleeps.append,        # 실제로 기다리지 않고 대기 시간만 기록
    )
    return client, session, sleeps


# ──────────────────────────────────────────────────────────────
# 정상 응답
# ──────────────────────────────────────────────────────────────

def test_successful_call_returns_parsed_response():
    client, session, sleeps = make_client([FakeResponse(200, ok_body("안녕하세요"))])

    result = client.complete("질문", system_prompt="시스템 지시")

    assert result.text == "안녕하세요"
    assert result.model == "test-model"
    assert result.stop_reason == "end_turn"
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    assert result.attempts == 1
    assert result.truncated is False
    assert sleeps == []


def test_request_follows_fuzzer_contract():
    """fuzzer/llm_client.py와 같은 URL·헤더·payload 구조로 보내는지 확인한다."""
    client, session, _ = make_client([FakeResponse(200, ok_body())])

    client.complete("질문", system_prompt="시스템 지시", max_tokens=50)

    call = session.calls[0]
    assert call["url"] == "https://monogpt.example.com/v1/messages"
    assert call["headers"]["x-api-key"] == FAKE_KEY
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["json"]["model"] == "test-model"
    assert call["json"]["max_tokens"] == 50
    assert call["json"]["system"] == "시스템 지시"
    assert call["json"]["messages"] == [{"role": "user", "content": "질문"}]


def test_optional_fields_are_omitted_when_not_given():
    client, session, _ = make_client([FakeResponse(200, ok_body())])

    client.complete("질문")

    payload = session.calls[0]["json"]
    assert "system" not in payload
    assert "temperature" not in payload
    assert payload["max_tokens"] == 1200   # 설정 기본값


def test_temperature_is_sent_when_given():
    client, session, _ = make_client([FakeResponse(200, ok_body())])
    client.complete("질문", temperature=0.0)
    assert session.calls[0]["json"]["temperature"] == 0.0


def test_multiple_text_blocks_are_joined_and_non_text_ignored():
    body = ok_body()
    body["content"] = [
        {"type": "text", "text": "앞부분 "},
        {"type": "tool_use", "id": "x"},
        {"type": "text", "text": "뒷부분"},
    ]
    client, _, _ = make_client([FakeResponse(200, body)])
    assert client.complete("질문").text == "앞부분 뒷부분"


def test_max_tokens_stop_reason_marks_truncated():
    client, _, _ = make_client([FakeResponse(200, ok_body(stop_reason="max_tokens"))])
    assert client.complete("질문").truncated is True


# ──────────────────────────────────────────────────────────────
# 재시도
# ──────────────────────────────────────────────────────────────

def test_rate_limit_then_success_uses_retry_after():
    client, session, sleeps = make_client([
        FakeResponse(429, text="rate limited", headers={"Retry-After": "3"}),
        FakeResponse(200, ok_body()),
    ])

    result = client.complete("질문")

    assert result.attempts == 2
    assert len(session.calls) == 2
    assert sleeps == [3.0]


def test_server_error_backoff_is_exponential():
    client, _, sleeps = make_client([
        FakeResponse(500, text="err"),
        FakeResponse(503, text="err"),
        FakeResponse(200, ok_body()),
    ])

    result = client.complete("질문")

    assert result.attempts == 3
    assert sleeps == [1.0, 2.0]


def test_timeout_then_success():
    client, _, _ = make_client([requests.Timeout(), FakeResponse(200, ok_body())])
    assert client.complete("질문").attempts == 2


def test_retries_exhausted_raises_last_error():
    client, session, sleeps = make_client([FakeResponse(500, text="err")] * 3)

    with pytest.raises(LLMHTTPError) as exc:
        client.complete("질문")

    assert exc.value.status_code == 500
    assert exc.value.attempts == 3          # 첫 시도 1 + 재시도 2
    assert len(session.calls) == 3
    assert len(sleeps) == 2                 # 마지막 실패 뒤에는 기다리지 않음


def test_rate_limit_exhausted_raises_rate_limit_error():
    client, _, _ = make_client([FakeResponse(429, text="slow down")] * 3)
    with pytest.raises(LLMRateLimitError):
        client.complete("질문")


def test_connection_error_exhausted_raises_transport_error():
    client, _, _ = make_client([requests.ConnectionError("boom")] * 3)
    with pytest.raises(LLMTransportError):
        client.complete("질문")


# ──────────────────────────────────────────────────────────────
# 영구 오류: 재시도하지 않는다
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_error_fails_immediately(status):
    client, session, sleeps = make_client([FakeResponse(status, text="bad")])

    with pytest.raises(LLMHTTPError) as exc:
        client.complete("질문")

    assert exc.value.status_code == status
    assert len(session.calls) == 1
    assert sleeps == []


# ──────────────────────────────────────────────────────────────
# 형식 오류: 빈 응답을 정상으로 넘기지 않는다
# ──────────────────────────────────────────────────────────────

def test_non_json_body_raises_format_error():
    client, _, _ = make_client([FakeResponse(200, ValueError("bad json"), text="<html>")])
    with pytest.raises(LLMResponseFormatError):
        client.complete("질문")


def test_missing_content_raises_format_error():
    client, _, _ = make_client([FakeResponse(200, {"model": "m"})])
    with pytest.raises(LLMResponseFormatError):
        client.complete("질문")


def test_empty_text_raises_format_error():
    client, _, _ = make_client([FakeResponse(200, ok_body(text="   "))])
    with pytest.raises(LLMResponseFormatError):
        client.complete("질문")


def test_empty_prompt_is_rejected_before_sending():
    client, session, _ = make_client([])
    with pytest.raises(ValueError):
        client.complete("   ")
    assert session.calls == []


# ──────────────────────────────────────────────────────────────
# 비밀값 비노출
# ──────────────────────────────────────────────────────────────

def test_api_key_never_appears_in_error_messages():
    outcomes = [FakeResponse(401, text="invalid key")]
    client, _, _ = make_client(outcomes)

    with pytest.raises(LLMHTTPError) as exc:
        client.complete("질문")

    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in repr(exc.value)


# ──────────────────────────────────────────────────────────────
# 호출 간격 제한
# ──────────────────────────────────────────────────────────────

def test_min_interval_waits_between_calls():
    now = [100.0]

    def clock() -> float:
        return now[0]

    session = FakeSession([FakeResponse(200, ok_body()), FakeResponse(200, ok_body())])
    sleeps: list[float] = []

    def sleep(sec: float) -> None:
        sleeps.append(sec)
        now[0] += sec

    client = LLMClient(
        make_settings(LLM_MIN_INTERVAL_SEC=2.0),
        session=session,  # type: ignore[arg-type]
        sleep=sleep,
        clock=clock,
    )

    client.complete("첫 번째")
    now[0] += 0.5                 # 0.5초만 지난 상태에서 두 번째 호출
    client.complete("두 번째")

    assert sleeps == [1.5]        # 남은 1.5초만 대기


def test_context_manager_closes_session():
    session = FakeSession([])
    with LLMClient(make_settings(), session=session):  # type: ignore[arg-type]
        pass
    assert session.closed is True
