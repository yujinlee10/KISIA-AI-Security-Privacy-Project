# orchestrator/coverage.py 테스트 — D1 Stage 9의 세는 기준

from __future__ import annotations

from orchestrator.coverage import PAIR_DENOMINATOR, QI_DENOMINATOR, CoverageRegistry, state_key


def test_denominators_match_d1():
    assert (QI_DENOMINATOR, PAIR_DENOMINATOR) == (8, 28)


def test_state_key_uses_qi_order_and_is_empty_for_empty_state():
    assert state_key({}) == ""
    assert state_key({"LOC": "CH", "AGE": "30s"}) == "AGE=30s|LOC=CH"


def test_first_disclosure_counts_state_and_transition_from_empty():
    registry = CoverageRegistry()
    event = registry.observe({}, {"LOC": "CH"}, 8, 5)

    assert event.new_qi == ("LOC",) and event.new_pairs == ()
    assert event.new_state and event.new_transition and event.transition_key == ">LOC=CH"
    assert event.k_decrease and event.interesting
    assert registry.summary()["unique_states"] == 1          # 빈 State는 세지 않는다


def test_turn_without_change_is_not_a_transition():
    registry = CoverageRegistry()
    registry.observe({}, {"LOC": "CH"}, 8, 5)
    event = registry.observe({"LOC": "CH"}, {"LOC": "CH"}, 5, 5)

    assert event.no_state_change and event.transition_key is None
    assert not (event.new_state or event.new_transition or event.k_decrease or event.interesting)
    assert registry.summary()["no_state_change_rate"] == 0.5


def test_pairs_and_refinement():
    registry = CoverageRegistry()
    registry.observe({}, {"LOC": "CH"}, 8, 5)
    pair = registry.observe({"LOC": "CH"}, {"LOC": "CH", "AGE": "30s"}, 5, 4)
    refine = registry.observe({"LOC": "CH", "AGE": "30s"}, {"LOC": "Zurich", "AGE": "30s"}, 4, 3)

    assert pair.new_pairs == ("AGE+LOC",) and pair.new_qi == ("AGE",)
    assert refine.new_qi == () and refine.new_pairs == ()             # QI 종류·쌍은 그대로
    assert refine.new_state and refine.new_transition                 # 값이 바뀌었으니 새 State
    summary = registry.summary()
    assert (summary["qi_coverage"], summary["pair_coverage"]) == (0.25, round(1 / 28, 4))
    assert (summary["unique_states"], summary["unique_transitions"]) == (3, 3)


def test_revisited_state_and_transition_are_not_new():
    registry = CoverageRegistry()
    registry.observe({}, {"LOC": "CH"}, 8, 5)
    again = registry.observe({}, {"LOC": "CH"}, 8, 5)          # 같은 등록부에 같은 전이가 다시 들어온 경우
    assert not again.new_state and not again.new_transition
    assert again.k_decrease and again.interesting               # K_DECREASE만으로도 Interesting


def test_zero_match_is_excluded_from_interesting():
    registry = CoverageRegistry()
    event = registry.observe({}, {"OCCP": "astronaut"}, 8, 0)
    assert event.zero_match and event.new_transition and not event.k_decrease and not event.interesting


def test_k_increase_or_same_is_not_k_decrease():
    registry = CoverageRegistry()
    assert not registry.observe({}, {}, 5, 5).k_decrease
    assert not registry.observe({}, {}, 5, 6).k_decrease


def test_discovery_curve_is_cumulative():
    registry = CoverageRegistry()
    registry.observe({}, {"LOC": "CH"}, 8, 5)
    registry.observe({"LOC": "CH"}, {"LOC": "CH"}, 5, 5)
    registry.observe({"LOC": "CH"}, {"LOC": "CH", "AGE": "30s"}, 5, 4)
    assert [p["states"] for p in registry.summary()["discovery_curve"]] == [1, 1, 2]
    assert registry.summary()["interesting_turns"] == 2
