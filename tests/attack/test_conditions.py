# attack/conditions.py 단위 테스트 — 레지스트리 조건식 해석·평가

from __future__ import annotations

import pytest

from attack.conditions import (
    Condition,
    ConditionParseError,
    Predicate,
    evaluate,
    has_descendant,
    parse_conditions,
)


# ── 해석 ──────────────────────────────────────────────────

def test_parse_o1_condition():
    conditions, note = parse_conditions("LOC not active AND prior_context_available = true")
    assert conditions == (
        Condition(Predicate.QI_NOT_ACTIVE, "LOC"),
        Condition(Predicate.PRIOR_CONTEXT_AVAILABLE),
    )
    assert note is None


def test_parse_o2_edu_condition_strips_parenthetical_note():
    text = ("EDU active AND active_node_has_descendant = true "
            "(current Core ontology: typically TERTIARY_EDUCATION) AND prior_context_available = true")
    conditions, note = parse_conditions(text)

    assert [c.predicate for c in conditions] == [
        Predicate.QI_ACTIVE, Predicate.ACTIVE_NODE_HAS_DESCENDANT, Predicate.PRIOR_CONTEXT_AVAILABLE,
    ]
    assert note == "current Core ontology: typically TERTIARY_EDUCATION"


@pytest.mark.parametrize("text", ["LOC maybe active", "NAME not active", "", "income > 3"])
def test_unknown_condition_fails(text):
    with pytest.raises(ConditionParseError):
        parse_conditions(text)


# ── 계층 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "attribute, value, expected",
    [
        ("LOC", "Switzerland", True),
        ("LOC", "Zurich, Switzerland", False),
        ("AGE", "adult", True),
        ("AGE", "30s", False),
        ("EDU", "higher_education", True),
        ("EDU", "Master's", False),
        ("REL", "partnered", True),
        ("OCCP", "software engineer", False),
    ],
)
def test_has_descendant(attribute, value, expected):
    assert has_descendant(attribute, value) is expected


# ── 평가 ──────────────────────────────────────────────────

O1_LOC = parse_conditions("LOC not active")[0]
O1_LOC_S4 = parse_conditions("LOC not active AND prior_context_available = true")[0]
O2_LOC = parse_conditions("LOC active AND active_node_has_descendant = true")[0]


def test_o1_applies_only_before_disclosure():
    assert evaluate(O1_LOC, "LOC", {}, 0) is True
    assert evaluate(O1_LOC, "LOC", {"LOC": "Switzerland"}, 1) is False


def test_s4_needs_prior_context():
    assert evaluate(O1_LOC_S4, "LOC", {}, 0) is False
    assert evaluate(O1_LOC_S4, "LOC", {}, 1) is True


def test_o2_applies_only_when_more_specific_value_exists():
    assert evaluate(O2_LOC, "LOC", {}, 1) is False                                    # 공개 전
    assert evaluate(O2_LOC, "LOC", {"LOC": "Switzerland"}, 1) is True                 # 국가 → 도시로 좁힐 여지
    assert evaluate(O2_LOC, "LOC", {"LOC": "Zurich, Switzerland"}, 1) is False        # 이미 도시
