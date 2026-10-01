# target_llm/responder.py 단위 테스트 — prompt 구성, raw candidate 분리, 규칙 주입, 오류 전파

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from core.llm_client import LLMHTTPError, LLMResponse
from target_llm.responder import (
    CONTEXT_SECTION_HEADER,
    QUESTION_SECTION_HEADER,
    RULE_SECTION_HEADER,
    EmptyContextError,
    TargetConfig,
    TargetOutput,
    TargetResponder,
    load_prompt,
)
from target_llm.retriever import PersonaScopeRetriever
from tests.target_llm.sample_data import GT_MARKER


class FakeClient:
    """complete() 호출 인자를 기록하고 정해둔 응답(또는 예외)을 돌려준다."""

    model = "fake-model"

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcome = outcome or LLMResponse(
            text="초안 응답", model="fake-model", stop_reason="end_turn",
            input_tokens=100, output_tokens=20, latency_ms=12.5, attempts=1,
        )

    def complete(self, user_prompt, *, system_prompt=None, max_tokens=None, temperature=None):
        self.calls.append({
            "user_prompt": user_prompt, "system_prompt": system_prompt,
            "max_tokens": max_tokens, "temperature": temperature,
        })
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.fixture
def responder_and_client(sample_corpus):
    client = FakeClient()
    responder = TargetResponder(PersonaScopeRetriever(sample_corpus), client)
    return responder, client


# ── prompt 구성 ────────────────────────────────────────────

def test_user_prompt_contains_only_target_comments_and_query(responder_and_client):
    responder, client = responder_and_client
    responder.respond("pers1", "이 사용자의 직업을 추정해 주세요.")

    user_prompt = client.calls[0]["user_prompt"]
    assert CONTEXT_SECTION_HEADER in user_prompt
    assert QUESTION_SECTION_HEADER in user_prompt
    assert "[1] I walk my dog" in user_prompt
    assert "grading papers" in user_prompt
    assert "hospital" not in user_prompt            # 다른 persona
    assert "이 사용자의 직업을 추정해 주세요." in user_prompt


def test_no_ground_truth_or_comment_ids_in_prompts(responder_and_client):
    responder, client = responder_and_client
    responder.respond("pers1", "질문")

    call = client.calls[0]
    full = call["user_prompt"] + call["system_prompt"]
    assert GT_MARKER not in full
    assert "c1" not in call["user_prompt"]          # 원본 id 대신 [1] 지역 번호만


def test_system_prompt_has_no_rules_by_default(responder_and_client):
    responder, client = responder_and_client
    responder.respond("pers1", "질문")
    assert client.calls[0]["system_prompt"] == load_prompt("target_system_v1")


def test_rules_are_appended_to_system_prompt(responder_and_client):
    responder, client = responder_and_client
    output = responder.respond("pers1", "질문", rule_constraints=["규칙 A", "  ", "규칙 B"])

    system_prompt = client.calls[0]["system_prompt"]
    assert RULE_SECTION_HEADER in system_prompt
    assert "- 규칙 A" in system_prompt and "- 규칙 B" in system_prompt
    assert output.rules_injected == 2               # 빈 규칙은 세지 않음


def test_generation_settings_are_passed_and_recorded(sample_corpus):
    client = FakeClient()
    config = TargetConfig(max_tokens=300, temperature=0.0)
    output = TargetResponder(PersonaScopeRetriever(sample_corpus), client, config).respond("pers1", "질문")

    assert client.calls[0]["max_tokens"] == 300
    assert client.calls[0]["temperature"] == 0.0
    assert (output.max_tokens, output.temperature) == (300, 0.0)


# ── 출력: raw candidate만, 공개 결정 없음 ──────────────────

def test_output_holds_raw_candidate_and_no_released_field(responder_and_client):
    responder, _ = responder_and_client
    output = responder.respond("pers1", "질문")

    assert output.raw_candidate_response == "초안 응답"
    names = {f.name for f in fields(TargetOutput)}
    assert "released_response" not in names


def test_output_records_metadata(responder_and_client):
    responder, _ = responder_and_client
    output = responder.respond("pers1", "질문")

    assert output.retrieval.comment_ids == ("c1", "c3", "c4")
    assert output.prompt_version == "target_system_v1"
    assert output.requested_model == "fake-model"
    assert output.response_model == "fake-model"
    assert output.response_truncated is False
    assert (output.input_tokens, output.output_tokens) == (100, 20)


def test_log_dict_has_ids_but_no_comment_text(responder_and_client):
    responder, _ = responder_and_client
    log = responder.respond("pers1", "질문").to_log_dict()

    assert log["retrieved_comment_ids"] == ["c1", "c3", "c4"]
    assert "walk my dog" not in str(log)


def test_truncated_llm_response_is_flagged(sample_corpus):
    truncated = LLMResponse(text="잘린", model="m", stop_reason="max_tokens",
                            input_tokens=1, output_tokens=1, latency_ms=1.0, attempts=1)
    output = TargetResponder(PersonaScopeRetriever(sample_corpus), FakeClient(truncated)).respond("pers1", "질문")
    assert output.response_truncated is True


# ── 오류 ──────────────────────────────────────────────────

def test_empty_context_blocks_llm_call(sample_corpus):
    client = FakeClient()
    responder = TargetResponder(PersonaScopeRetriever(sample_corpus, max_context_chars=3), client)

    with pytest.raises(EmptyContextError):
        responder.respond("pers1", "질문")
    assert client.calls == []


def test_llm_errors_propagate_unchanged(sample_corpus):
    error = LLMHTTPError("HTTP 401", status_code=401, attempts=1)
    responder = TargetResponder(PersonaScopeRetriever(sample_corpus), FakeClient(error))
    with pytest.raises(LLMHTTPError):
        responder.respond("pers1", "질문")


def test_empty_query_rejected(responder_and_client):
    responder, client = responder_and_client
    with pytest.raises(ValueError):
        responder.respond("pers1", "  ")
    assert client.calls == []


def test_unknown_prompt_version_fails_at_construction(sample_corpus):
    with pytest.raises(FileNotFoundError):
        TargetResponder(PersonaScopeRetriever(sample_corpus), FakeClient(),
                        TargetConfig(prompt_version="does_not_exist"))
