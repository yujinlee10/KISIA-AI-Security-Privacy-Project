# qi_extractor/evidence.py 단위 테스트 — 근거 위치 찾기, 질문 반복 판정

from __future__ import annotations

import unicodedata

from qi_extractor.evidence import is_query_echo, locate_span, normalize_text

RESPONSE = "기록을 보면 이 사용자는 취리히에서 생활하는 것으로 보입니다.\n직업은  소프트웨어 엔지니어입니다."


# ── locate_span ───────────────────────────────────────────

def test_exact_span_is_found_with_offsets():
    span = "취리히에서 생활하는 것으로 보입니다"
    start, end = locate_span(span, RESPONSE)
    assert RESPONSE[start:end] == span


def test_surrounding_quotes_are_ignored():
    start, end = locate_span("“취리히에서 생활하는”", RESPONSE)
    assert RESPONSE[start:end] == "취리히에서 생활하는"


def test_whitespace_difference_is_tolerated():
    # 원문은 '직업은' 뒤 공백 2칸, LLM은 1칸으로 돌려줌
    start, end = locate_span("직업은 소프트웨어 엔지니어입니다", RESPONSE)
    assert RESPONSE[start:end] == "직업은  소프트웨어 엔지니어입니다"


def test_changed_characters_are_not_found():
    assert locate_span("취리히에 살고 있습니다", RESPONSE) is None      # 원문에 없는 표현
    assert locate_span("소프트웨어 개발자", RESPONSE) is None


def test_empty_span_is_not_found():
    assert locate_span("   ", RESPONSE) is None
    assert locate_span("\"\"", RESPONSE) is None


def test_nfd_hangul_matches_after_normalization():
    nfd_span = unicodedata.normalize("NFD", "취리히에서")
    assert locate_span(nfd_span, normalize_text(RESPONSE)) is not None


# ── is_query_echo ─────────────────────────────────────────

def test_span_only_repeating_query_is_echo():
    assert is_query_echo(["30대"], "이 사용자는 30대인가요?") is True


def test_span_with_new_wording_is_not_echo():
    assert is_query_echo(["네, 서울에 삽니다"], "혹시 서울에 사시나요?") is False


def test_any_new_span_is_enough():
    assert is_query_echo(["30대", "30대 중반으로 보입니다"], "30대인가요?") is False


def test_echo_ignores_whitespace_difference():
    assert is_query_echo(["서울  거주"], "서울 거주 맞나요?") is True


def test_no_spans_is_not_echo():
    assert is_query_echo([], "질문") is False
