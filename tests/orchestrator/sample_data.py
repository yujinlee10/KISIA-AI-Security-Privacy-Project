# orchestrator 테스트용 도우미
#
# 팀원 코드(risk_defender, fuzzer feedback·mutation, evaluation 로그 스키마)와 우리 쪽
# 추출 검증·정규화·Seed 레지스트리는 실제 것을 쓴다. 가짜로 바꾸는 것은 LLM 호출뿐이다.
#
# 기준 집단은 tests/adapters/test_risk_adapter.py와 같은 8명이다 (k를 손으로 계산할 수 있다).
#   id  AGE  LOC                  OCCP               EDU             REL
#   p1  30s  Zurich, Switzerland  software engineer  Master's        single      ← 대상
#   p2  30s  Zurich, Switzerland  nurse              College Degree  single
#   p3  30s  Zurich, Switzerland  chef               College Degree  married
#   p4  40s  Geneva, Switzerland  software engineer  PhD             married
#   p5  30s  Geneva, Switzerland  software engineer  Master's        divorced
#   p6  20s  Berlin, Germany      software engineer  Master's        single
#   p7  50s  Berlin, Germany      nurse              HS Diploma      widowed
#   p8  30s  Munich, Germany      teacher            College Degree  married

from __future__ import annotations

import json
import re
from typing import Any, Callable

from adapters.fuzzer_adapter import _load_system_prompt as load_fuzzer_prompt
from adapters.risk_adapter import PopulationMode, ReferencePopulation
from core.llm_client import LLMResponse
from orchestrator.rewriter import load_prompt as load_rewriter_prompt
from qi_extractor.extractor import DEFAULT_PROMPT_VERSION as DEFAULT_EXTRACTOR_PROMPT
from qi_extractor.extractor import RESPONSE_SECTION_HEADER
from qi_extractor.extractor import load_prompt as load_extractor_prompt
from risk_defender.loader import PersonaRecord
from target_llm.corpus import PersonaComment
from target_llm.responder import load_prompt as load_target_prompt
from target_llm.retriever import RetrievalResult

ROWS = [
    ("p1", "30s", "Zurich, Switzerland", "software engineer", "Master's", "single"),
    ("p2", "30s", "Zurich, Switzerland", "nurse", "College Degree", "single"),
    ("p3", "30s", "Zurich, Switzerland", "chef", "College Degree", "married"),
    ("p4", "40s", "Geneva, Switzerland", "software engineer", "PhD", "married"),
    ("p5", "30s", "Geneva, Switzerland", "software engineer", "Master's", "divorced"),
    ("p6", "20s", "Berlin, Germany", "software engineer", "Master's", "single"),
    ("p7", "50s", "Berlin, Germany", "nurse", "HS Diploma", "widowed"),
    ("p8", "30s", "Munich, Germany", "teacher", "College Degree", "married"),
]

TARGET_ID = "p1"


# risk_defender 값(위 표)과 같은 사람들을 SynthPAI profile 형식으로 적은 것. 온톨로지 백엔드가 읽는다.
#   나이는 같은 10년 구간 안에서 서로 다르게 두었다 (정확한 나이·5년 구간 단계를 시험하기 위해).
RAW_PROFILES = {
    "p1": {"age": 34, "sex": "male", "city_country": "Zurich, Switzerland", "birth_city_country": "Berlin, Germany",
           "education": "Masters in Computer Science", "occupation": "software engineer",
           "income_level": "high", "relationship_status": "single"},
    "p2": {"age": 31, "sex": "female", "city_country": "Zurich, Switzerland", "birth_city_country": "Zurich, Switzerland",
           "education": "Bachelors in Nursing", "occupation": "nurse",
           "income_level": "middle", "relationship_status": "single"},
    "p3": {"age": 37, "sex": "male", "city_country": "Zurich, Switzerland", "birth_city_country": "Geneva, Switzerland",
           "education": "Bachelors in Culinary Arts", "occupation": "chef",
           "income_level": "middle", "relationship_status": "married"},
    "p4": {"age": 45, "sex": "female", "city_country": "Geneva, Switzerland", "birth_city_country": "Geneva, Switzerland",
           "education": "PhD in Computer Science", "occupation": "software engineer",
           "income_level": "very high", "relationship_status": "married"},
    "p5": {"age": 33, "sex": "male", "city_country": "Geneva, Switzerland", "birth_city_country": "Munich, Germany",
           "education": "Masters in Data Science", "occupation": "software engineer",
           "income_level": "high", "relationship_status": "divorced"},
    "p6": {"age": 28, "sex": "female", "city_country": "Berlin, Germany", "birth_city_country": "Berlin, Germany",
           "education": "Masters in Computer Engineering", "occupation": "software engineer",
           "income_level": "high", "relationship_status": "single"},
    "p7": {"age": 52, "sex": "female", "city_country": "Berlin, Germany", "birth_city_country": "Munich, Germany",
           "education": "High School Diploma", "occupation": "nurse",
           "income_level": "low", "relationship_status": "widowed"},
    "p8": {"age": 39, "sex": "male", "city_country": "Munich, Germany", "birth_city_country": "Berlin, Germany",
           "education": "Bachelors in Education", "occupation": "teacher",
           "income_level": "middle", "relationship_status": "married"},
}


def make_population() -> ReferencePopulation:
    records = [
        PersonaRecord(person_id=pid, username=None,
                      qis={"AGE": a, "LOC": l, "OCCP": o, "EDU": e, "REL": r},
                      raw_profile=RAW_PROFILES[pid], source_texts=[])
        for pid, a, l, o, e, r in ROWS
    ]
    return ReferencePopulation(records, PopulationMode.BASE, "test", None)


# ── Target 응답 대본과, 그 문장에서 "추출기 LLM"이 내놓을 관찰 ─────────────
# (응답에 이 문구가 있으면 → 이 관찰을 낸다). 근거 문구는 응답에 실제로 있어야 추출 검증을 통과한다.
PHRASE_OBSERVATIONS: list[tuple[str, dict[str, Any]]] = [
    ("취리히에 거주", {"attribute": "LOC", "raw_value": "취리히", "value_en": "Zurich"}),
    ("스위스에 거주", {"attribute": "LOC", "raw_value": "스위스", "value_en": "Switzerland"}),
    ("34세", {"attribute": "AGE", "raw_value": "34세", "value_en": "34"}),
    ("30대", {"attribute": "AGE", "raw_value": "30대", "value_en": "30s"}),
    ("성인", {"attribute": "AGE", "raw_value": "성인", "value_en": "adult"}),
    ("소프트웨어 엔지니어", {"attribute": "OCCP", "raw_value": "소프트웨어 엔지니어", "value_en": "software engineer"}),
    ("정보통신기술 전문가", {"attribute": "OCCP", "raw_value": "정보통신기술 전문가",
                     "value_en": "Information and communications technology professionals"}),
    ("석사 학위", {"attribute": "EDU", "raw_value": "석사", "value_en": "Master's degree"}),
    ("대학 이상의 학력", {"attribute": "EDU", "raw_value": "대학 이상의 학력", "value_en": "higher education"}),
    ("미혼", {"attribute": "REL", "raw_value": "미혼", "value_en": "single"}),
    ("여성", {"attribute": "SEX", "raw_value": "여성", "value_en": "female"}),
    ("남성", {"attribute": "SEX", "raw_value": "남성", "value_en": "male"}),
]

RAW_T1 = "기록을 보면 이 사용자는 스위스에 거주하는 것으로 보입니다."
RAW_T2 = "글의 내용으로 보아 30대이고 소프트웨어 엔지니어로 일합니다."
RAW_T3 = "석사 학위가 있고 미혼인 것으로 보입니다."
RAW_ZURICH = "기록을 보면 이 사용자는 취리히에 거주하는 것으로 보입니다."
RAW_AGE = "글의 내용으로 보아 이 사용자는 30대입니다."

# "Rewrite LLM"의 동작: 수정 지시(삭제·일반화)를 읽어 문장을 고친다.
#   삭제   : 아래 표의 (지울 구절 → 대신 쓸 구절). 표에 없으면 값 단어만 지운다
#   일반화 : 값 단어를 "바꿀 표현"의 한국어 부분으로 바꾼다
DELETE_EDITS: dict[str, tuple[str, str]] = {
    "소프트웨어 엔지니어": ("이고 소프트웨어 엔지니어로 일합니다", "입니다"),
    "석사": ("석사 학위가 있고 ", ""),
    "미혼": ("미혼인 것으로 보입니다", "관계 상태는 기록에서 확인하기 어렵습니다"),
    "취리히": ("취리히에 거주하는 것으로 보입니다", "거주 지역은 기록에서 확인하기 어렵습니다"),
    "스위스": ("스위스에 거주하는 것으로 보입니다", "거주 지역은 기록에서 확인하기 어렵습니다"),
    "30대": ("30대", "나이를 알 수 없는 분"),
}

_INSTRUCTION = re.compile(
    r'^\d+\. (삭제|일반화) \| 속성: \w+ \| 응답에 나온 값: "([^"]*)".*?(?: \| 바꿀 표현: (.*))?$', re.MULTILINE)


def apply_instructions(prompt: str) -> str:
    """Rewrite LLM user prompt([원래 응답] + [수정 지시])를 받아 고쳐 쓴 문장을 만든다."""
    text = prompt.split("[원래 응답]\n", 1)[1].split("\n\n[수정 지시]", 1)[0]
    for action, value, phrase in _INSTRUCTION.findall(prompt):
        if action == "삭제":
            old, new = DELETE_EDITS.get(value, (value, ""))
            text = text.replace(old, new)
        else:
            text = text.replace(value, phrase.split(" (")[0])
    return text


def llm(text: str, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(text=text, model="fake-model", stop_reason=stop_reason,
                       input_tokens=10, output_tokens=5, latency_ms=1.0, attempts=1)


def extract_from(response_text: str) -> str:
    """응답 문장에 들어 있는 문구로 추출기 JSON 출력을 만든다."""
    observations = [
        {**fields, "inference_mode": "EXPLICIT", "confidence": 5, "evidence_spans": [phrase]}
        for phrase, fields in PHRASE_OBSERVATIONS if phrase in response_text
    ]
    return json.dumps({"observations": observations}, ensure_ascii=False)


class RoleClient:
    """
    system prompt로 어느 역할의 호출인지 가려 응답하는 가짜 LLM client.

    target_script     : Target이 턴마다 낼 응답 (순서대로)
    rewrites          : Rewrite LLM 동작을 바꿀 때만 넣는다. (원문) → 결과 함수. 기본은 apply_instructions
    extractor         : 추출기 동작. 기본은 PHRASE_OBSERVATIONS 기반
    fail_on           : (역할, 그 역할의 몇 번째 호출) → 던질 예외
    calls             : 모든 호출 기록 (역할 포함)
    """
    model = "fake-model"

    def __init__(self, target_script: list[str], *, rewrites: Callable[[str], str] | None = None,
                 extractor: Callable[[str], str] | None = None,
                 fail_on: dict[tuple[str, int], Exception] | None = None) -> None:
        self._target_script = list(target_script)
        self._rewrites = rewrites
        self._extractor = extractor or extract_from
        self._fail_on = fail_on or {}
        self._prompts = {
            "fuzzer": load_fuzzer_prompt(),
            "target": load_target_prompt("target_system_v1"),
            "extractor": load_extractor_prompt(DEFAULT_EXTRACTOR_PROMPT),
            "rewriter": load_rewriter_prompt("rewriter_system_v1"),
        }
        self.calls: list[dict[str, Any]] = []

    def count(self, role: str) -> int:
        return sum(1 for call in self.calls if call["role"] == role)

    def prompts_of(self, role: str) -> list[str]:
        return [call["user_prompt"] for call in self.calls if call["role"] == role]

    def complete(self, user_prompt, *, system_prompt=None, max_tokens=None, temperature=None):
        role = next(name for name, prompt in self._prompts.items() if (system_prompt or "").startswith(prompt))
        self.calls.append({"role": role, "user_prompt": user_prompt, "system_prompt": system_prompt,
                           "temperature": temperature})
        number = self.count(role)
        if (role, number) in self._fail_on:
            raise self._fail_on[(role, number)]

        if role == "fuzzer":
            return llm(f"변형 질문 {number}번")
        if role == "target":
            return llm(self._target_script[number - 1])
        if role == "extractor":
            return llm(self._extractor(user_prompt.split(RESPONSE_SECTION_HEADER, 1)[1]))
        if self._rewrites is not None:
            original = user_prompt.split("[원래 응답]\n", 1)[1].split("\n\n[수정 지시]", 1)[0]
            return llm(self._rewrites(original))
        return llm(apply_instructions(user_prompt))


class OneCommentRetriever:
    """Target 문맥에 글 하나만 넣는 검색기 (Retriever 인터페이스)."""
    name = "test_retriever"
    version = "0"

    def retrieve(self, target_id: str, query: str) -> RetrievalResult:
        comment = PersonaComment(comment_id="c1", target_id=target_id, text="sample comment", position=0)
        return RetrievalResult(target_id, (comment,), 1, len(comment.text), False, self.name, self.version)
