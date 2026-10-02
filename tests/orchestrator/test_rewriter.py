# orchestrator/rewriter.py 테스트 — 지시문 조립과 결과 검사

from __future__ import annotations

import pytest

from orchestrator.rewriter import (
    GENERALIZED_PHRASES,
    ResponseRewriter,
    RewriteAction,
    RewriteError,
    RewriteInstruction,
    build_user_prompt,
    replacement_phrase,
)
from normalizer.rules import GENERALIZED_NODES, age_candidates, edu_candidates, rel_candidates
from tests.orchestrator.sample_data import llm

GENERALIZE = RewriteInstruction("t1-o1", "LOC", RewriteAction.GENERALIZE, "취리히",
                                ("취리히에 거주",), replacement="Switzerland")
SUPPRESS = RewriteInstruction("t1-o2", "REL", RewriteAction.SUPPRESS, "미혼", ("미혼인 것으로",))


class OneShotClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, user_prompt, *, system_prompt=None, max_tokens=None, temperature=None):
        self.calls.append({"user_prompt": user_prompt, "system_prompt": system_prompt, "temperature": temperature})
        return self.response


def test_user_prompt_lists_each_instruction():
    prompt = build_user_prompt("원래 문장", [GENERALIZE, SUPPRESS])

    assert "[원래 응답]\n원래 문장" in prompt
    assert '1. 일반화 | 속성: LOC | 응답에 나온 값: "취리히" | 근거 문구: "취리히에 거주" | 바꿀 표현: Switzerland' in prompt
    assert '2. 삭제 | 속성: REL | 응답에 나온 값: "미혼"' in prompt
    assert "바꿀 표현" not in prompt.splitlines()[-1]          # 삭제 지시에는 바꿀 표현이 없다


def test_generalized_node_names_become_readable_phrases():
    assert replacement_phrase("higher_education") == "대학 이상의 학력 (higher education)"
    assert replacement_phrase("Switzerland") == "Switzerland"


def test_every_risk_defender_node_has_a_phrase_that_normalizes_back():
    """고쳐 쓴 응답을 다시 추출했을 때 같은 일반화 노드로 읽혀야 State에 제대로 반영된다."""
    rules = {"AGE": age_candidates, "EDU": edu_candidates, "REL": rel_candidates}
    for attribute, nodes in GENERALIZED_NODES.items():
        for node in nodes:
            korean, english = GENERALIZED_PHRASES[node]
            candidates = rules[attribute](korean, english)
            if node == "young":
                assert candidates == {"teens"}      # risk_defender에서 young과 teens는 같은 사람들을 가리킨다
            else:
                assert candidates == {node}


def test_rewrite_returns_text_and_uses_fixed_temperature():
    client = OneShotClient(llm("  고쳐 쓴 문장  "))
    result = ResponseRewriter(client).rewrite("원래 문장", [GENERALIZE])

    assert result.text == "고쳐 쓴 문장"
    assert result.instructions == (GENERALIZE,)
    assert client.calls[0]["temperature"] == 0.0


def test_empty_instructions_are_rejected():
    with pytest.raises(ValueError):
        ResponseRewriter(OneShotClient(llm("x"))).rewrite("원래 문장", [])


@pytest.mark.parametrize("response", [llm("   "), llm("잘린 문장", stop_reason="max_tokens")])
def test_unusable_output_raises(response):
    with pytest.raises(RewriteError):
        ResponseRewriter(OneShotClient(response)).rewrite("원래 문장", [SUPPRESS])
