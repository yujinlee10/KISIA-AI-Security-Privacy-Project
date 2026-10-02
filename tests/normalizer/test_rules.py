# normalizer/rules.py 단위 테스트 — 한국어·영어 표현 해석

from __future__ import annotations

import pytest

from normalizer.rules import age_candidates, clean_free_text, edu_candidates, rel_candidates


@pytest.mark.parametrize(
    "raw_value, value_en, expected",
    [
        ("30대", None, {"30s"}),
        ("30대 중반", "mid-30s", {"30s"}),
        ("34세", "34 years old", {"30s"}),
        ("서른넷", "34", {"30s"}),
        ("10대", "teenager", {"teens"}),
        ("in his forties", None, {"40s"}),
        ("30대 후반에서 40대 초반", "late 30s to early 40s", {"30s", "40s"}),   # 모호 → PENDING 대상
        ("성인", "adult", {"adult"}),
        ("노년층", None, {"senior"}),
        ("젊은 편", "young-ish", set()),
    ],
)
def test_age(raw_value, value_en, expected):
    assert age_candidates(raw_value, value_en) == expected


def test_age_does_not_read_year_as_decade():
    assert age_candidates("1990년대생", "born in the 1990s") == set()


@pytest.mark.parametrize(
    "raw_value, value_en, expected",
    [
        ("석사", "Master's degree", {"Master's"}),
        ("박사", "Ph.D.", {"PhD"}),
        ("학사 학위", "Bachelor's degree", {"College Degree"}),
        ("대졸", None, {"College Degree"}),
        ("고졸", "high school graduate", {"HS Diploma"}),
        ("대학생", "college student", {"In College/HS Diploma"}),
        ("석사 과정 재학 중", "studying for a master's", {"Master's", "In College/HS Diploma"}),
        ("고등교육 이수", "higher education", {"higher_education"}),
        ("잘 모름", None, set()),
    ],
)
def test_edu(raw_value, value_en, expected):
    assert edu_candidates(raw_value, value_en) == expected


@pytest.mark.parametrize(
    "raw_value, value_en, expected",
    [
        ("미혼", "single", {"single"}),
        ("결혼하지 않은 상태", "unmarried", {"single"}),
        ("기혼", "married", {"married"}),
        ("약혼", "engaged", {"engaged"}),
        ("연애 중", "in a relationship", {"in a relationship"}),
        ("여자친구가 있음", "has a girlfriend", {"in a relationship"}),
        ("이혼", "divorced", {"divorced"}),
        ("사별", "widowed", {"widowed"}),
    ],
)
def test_rel(raw_value, value_en, expected):
    assert rel_candidates(raw_value, value_en) == expected


def test_rel_unmarried_is_not_married():
    assert "married" not in rel_candidates("미혼", "unmarried")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Software Engineer", "software engineer"),
        ("a  software   engineer.", "software engineer"),
        ("The nurse", "nurse"),
    ],
)
def test_clean_free_text(text, expected):
    assert clean_free_text(text) == expected
