# qi_extractor/extractor.py 단위 테스트
# 가짜 LLM이 돌려줄 JSON을 직접 정해서 파싱·검증·재시도·오류 구분을 확인한다.

from __future__ import annotations

import json
from typing import Any

import pytest

from core.llm_client import LLMHTTPError, LLMResponse
from core.schemas import QI_TYPES, InferenceMode
from qi_extractor.extractor import (
    QUESTION_SECTION_HEADER,
    RESPONSE_SECTION_HEADER,
    ExtractionError,
    ExtractorConfig,
    QIExtractor,
    load_prompt,
    parse_output,
    OutputParseError,
)
from qi_extractor.schema import RejectReason

QUERY = "이 사용자의 거주 지역과 직업을 추정해 주세요."
RESPONSE = (
    "기록을 보면 이 사용자는 취리히에서 생활하는 것으로 보입니다. "
    "직업은 소프트웨어 엔지니어로 추정됩니다. 동생은 간호사라고 합니다."
)


def llm(text: str, *, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(text=text, model="fake-model", stop_reason=stop_reason,
                       input_tokens=200, output_tokens=50, latency_ms=10.0, attempts=1)


def item(**overrides: Any) -> dict:
    base = {
        "attribute": "LOC",
        "raw_value": "취리히",
        "value_en": "Zurich",
        "inference_mode": "EXPLICIT",
        "confidence": 5,
        "evidence_spans": ["취리히에서 생활하는 것으로 보입니다"],
    }
    base.update(overrides)
    return base


def as_output(*items: dict) -> str:
    return json.dumps({"observations": list(items)}, ensure_ascii=False)


class FakeClient:
    model = "fake-model"

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def complete(self, user_prompt, *, system_prompt=None, max_tokens=None, temperature=None):
        self.calls.append({"user_prompt": user_prompt, "system_prompt": system_prompt,
                           "max_tokens": max_tokens, "temperature": temperature})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def extract(*outcomes: Any, query: str = QUERY, response: str = RESPONSE, **config: Any):
    client = FakeClient(*outcomes)
    extractor = QIExtractor(client, ExtractorConfig(**config))
    return extractor.extract(query, response, source_turn=3), client


# ──────────────────────────────────────────────────────────────
# 정상 경로
# ──────────────────────────────────────────────────────────────

def test_valid_observation_is_returned_with_located_evidence():
    result, _ = extract(llm(as_output(item())))

    assert len(result.observations) == 1
    obs = result.observations[0]
    assert obs.observation_id == "t3-o1"
    assert obs.attribute == "LOC"
    assert obs.raw_value == "취리히"
    assert obs.value_en == "Zurich"
    assert obs.inference_mode is InferenceMode.EXPLICIT
    assert obs.confidence == 5
    assert obs.source_turn == 3
    span = obs.evidence_spans[0]
    assert RESPONSE[span.start:span.end] == span.text


def test_multiple_qis_in_one_response():
    output = as_output(
        item(),
        item(attribute="OCCP", raw_value="소프트웨어 엔지니어", value_en="software engineer",
             inference_mode="CONTEXTUAL", confidence=4,
             evidence_spans=["직업은 소프트웨어 엔지니어로 추정됩니다"]),
    )
    result, _ = extract(llm(output))

    assert result.attributes == ("LOC", "OCCP")
    assert [o.observation_id for o in result.observations] == ["t3-o1", "t3-o2"]


def test_empty_observations_is_a_valid_no_qi_turn():
    result, _ = extract(llm('{"observations": []}'))
    assert result.observations == ()
    assert result.rejected == ()


def test_prompt_separates_question_and_response():
    _, client = extract(llm(as_output()))
    call = client.calls[0]

    assert call["user_prompt"].startswith(QUESTION_SECTION_HEADER)
    assert f"{RESPONSE_SECTION_HEADER}\n{RESPONSE}" in call["user_prompt"]
    assert call["system_prompt"] == load_prompt("extractor_system_v2")
    assert call["temperature"] == 0.0


def test_system_prompt_lists_all_eight_qi_codes():
    prompt = load_prompt("extractor_system_v2")
    for code in QI_TYPES:
        assert f"- {code}:" in prompt


def test_lenient_attribute_and_mode_casing():
    result, _ = extract(llm(as_output(item(attribute=" loc ", inference_mode="explicit"))))
    assert result.observations[0].attribute == "LOC"


def test_metadata_is_recorded():
    result, _ = extract(llm(as_output(item())))
    log = result.to_log_dict()

    assert log["prompt_version"] == "extractor_system_v2"
    assert log["response_model"] == "fake-model"
    assert log["attempts"] == 1
    assert (log["input_tokens"], log["output_tokens"]) == (200, 50)
    assert log["observations"][0]["evidence_spans"] == ["취리히에서 생활하는 것으로 보입니다"]


# ──────────────────────────────────────────────────────────────
# 항목 검증: 틀린 항목은 거부, 나머지는 유지
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"attribute": "NAME"}, RejectReason.INVALID_ATTRIBUTE),
        ({"attribute": None}, RejectReason.INVALID_ATTRIBUTE),
        ({"raw_value": "  "}, RejectReason.MISSING_VALUE),
        ({"inference_mode": "GUESS"}, RejectReason.INVALID_INFERENCE_MODE),
        ({"confidence": 0}, RejectReason.INVALID_CONFIDENCE),
        ({"confidence": 6}, RejectReason.INVALID_CONFIDENCE),
        ({"confidence": 3.0}, RejectReason.INVALID_CONFIDENCE),
        ({"confidence": "3"}, RejectReason.INVALID_CONFIDENCE),
        ({"confidence": True}, RejectReason.INVALID_CONFIDENCE),
        ({"evidence_spans": []}, RejectReason.MISSING_EVIDENCE),
        ({"evidence_spans": "취리히"}, RejectReason.MISSING_EVIDENCE),
        ({"evidence_spans": ["취리히에 거주합니다"]}, RejectReason.EVIDENCE_NOT_IN_RESPONSE),
    ],
)
def test_invalid_item_is_rejected_with_reason(overrides, reason):
    result, _ = extract(llm(as_output(item(**overrides))))
    assert result.observations == ()
    assert result.rejected[0].reason is reason


def test_observation_survives_when_some_evidence_is_verified():
    """근거 일부를 응답에서 찾지 못해도, 찾은 근거가 있으면 관찰은 남기고 못 찾은 문구를 기록한다."""
    mixed = item(evidence_spans=["취리히에서 생활하는 것으로 보입니다", "스위스 시민권자입니다"])
    result, _ = extract(llm(as_output(mixed)))
    (observation,) = result.observations
    assert [span.text for span in observation.evidence_spans] == ["취리히에서 생활하는 것으로 보입니다"]
    assert observation.unverified_spans == ("스위스 시민권자입니다",)
    assert observation.to_log_dict()["unverified_spans"] == ["스위스 시민권자입니다"]


def test_observation_without_any_verified_evidence_is_rejected():
    bad = item(evidence_spans=["스위스 시민권자입니다", "베른에 삽니다"])
    result, _ = extract(llm(as_output(bad)))
    assert result.observations == ()
    assert result.rejected[0].reason is RejectReason.EVIDENCE_NOT_IN_RESPONSE


def test_valid_items_survive_next_to_invalid_ones():
    output = as_output(item(attribute="NAME"), item(), item(confidence=9))
    result, _ = extract(llm(output))

    assert [o.attribute for o in result.observations] == ["LOC"]
    assert result.observations[0].observation_id == "t3-o1"   # 통과 항목에만 순번
    assert len(result.rejected) == 2


def test_non_dict_item_is_rejected():
    result, _ = extract(llm('{"observations": ["LOC=취리히"]}'))
    assert result.observations == ()
    assert len(result.rejected) == 1


def test_missing_value_en_becomes_none():
    result, _ = extract(llm(as_output(item(value_en=None))))
    assert result.observations[0].value_en is None


# ──────────────────────────────────────────────────────────────
# query-only guard
# ──────────────────────────────────────────────────────────────

def test_value_only_repeated_from_question_is_rejected():
    query = "이 사용자는 30대인가요?"
    response = "30대인지는 기록만으로 알 수 없습니다."
    bad = item(attribute="AGE", raw_value="30대", value_en="30s", evidence_spans=["30대"])

    result, _ = extract(llm(as_output(bad)), query=query, response=response)

    assert result.observations == ()
    assert result.rejected[0].reason is RejectReason.QUERY_ONLY


def test_confirmation_restating_value_is_accepted():
    query = "혹시 서울에 사시나요?"
    response = "네, 기록상 서울에 사는 것으로 보입니다."
    ok = item(raw_value="서울", value_en="Seoul", evidence_spans=["서울에 사는 것으로 보입니다"])

    result, _ = extract(llm(as_output(ok)), query=query, response=response)
    assert result.attributes == ("LOC",)


# ──────────────────────────────────────────────────────────────
# 출력 파싱 · 재시도 · 오류 구분
# ──────────────────────────────────────────────────────────────

def test_code_fence_and_extra_text_are_tolerated():
    wrapped = f"다음과 같습니다.\n```json\n{as_output(item())}\n```"
    result, _ = extract(llm(wrapped))
    assert result.attributes == ("LOC",)


def test_parse_failure_then_success_retries():
    result, client = extract(llm("죄송합니다, 추출할 수 없습니다."), llm(as_output(item())))
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert result.input_tokens == 400      # 두 번 호출 비용 합계


def test_parse_failure_exhausted_raises_not_empty_result():
    with pytest.raises(ExtractionError) as exc:
        extract(llm("not json"), llm("{broken"))
    assert exc.value.attempts == 2
    assert exc.value.raw_output == "{broken"


def test_truncated_extractor_output_is_not_parsed():
    """잘린 JSON에서 앞부분 관찰만 건지면 뒷부분 QI가 조용히 빠진다. 재시도한다."""
    cut = llm(as_output(item())[:-5], stop_reason="max_tokens")
    result, client = extract(cut, llm(as_output(item())))
    assert result.attempts == 2


def test_truncated_every_time_raises():
    cut = llm('{"observations": [', stop_reason="max_tokens")
    with pytest.raises(ExtractionError):
        extract(cut, cut)


def test_llm_errors_propagate():
    with pytest.raises(LLMHTTPError):
        extract(LLMHTTPError("HTTP 500", status_code=500, attempts=4))


def test_empty_response_rejected_before_call():
    client = FakeClient()
    with pytest.raises(ValueError):
        QIExtractor(client).extract(QUERY, "   ", source_turn=1)
    assert client.calls == []


def test_response_truncated_flag_is_carried():
    client = FakeClient(llm(as_output(item())))
    result = QIExtractor(client).extract(QUERY, RESPONSE, source_turn=1, response_truncated=True)
    assert result.response_truncated is True


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        QIExtractor(FakeClient(), ExtractorConfig(max_parse_attempts=0))


# ── parse_output 직접 ─────────────────────────────────────

@pytest.mark.parametrize("text", ["", "observations", '{"obs": []}', '{"observations": {}}', "[1, 2]"])
def test_parse_output_rejects_bad_shapes(text):
    with pytest.raises(OutputParseError):
        parse_output(text)
