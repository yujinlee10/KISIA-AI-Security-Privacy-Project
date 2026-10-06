# attack/selector.py 테스트 — State에 맞는 Seed 고르기와 플레이스홀더 채우기

from __future__ import annotations

import pytest

from attack.registry import SeedRegistry
from attack.selector import AttackStateView, BindingError, SeedSelector


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


def selector(registry, seed=7):
    return SeedSelector(registry, rng_seed=seed)


def test_first_turn_offers_only_o1_without_follow_up(registry):
    seeds = selector(registry).applicable(AttackStateView({}, prior_turns=0))
    assert {s.objective for s in seeds} == {"O1"}
    assert all(s.scenario_code != "S4" for s in seeds)      # 앞선 대화가 없으니 S4 제외
    assert len(seeds) == 72 - 24


def test_disclosed_city_removes_loc_seeds(registry):
    view = AttackStateView({"LOC": "Zurich, Switzerland"}, prior_turns=1)
    seeds = selector(registry).applicable(view)
    assert not any(s.target_qi == "LOC" for s in seeds)     # O1은 공개됨, O2는 더 좁힐 곳 없음


def test_disclosed_country_enables_loc_refinement(registry):
    view = AttackStateView({"LOC": "Switzerland"}, prior_turns=1)
    loc = [s for s in selector(registry).applicable(view) if s.target_qi == "LOC"]
    assert loc and all(s.objective == "O2" for s in loc)


def test_choice_is_reproducible_with_same_seed(registry):
    view = AttackStateView({}, 0)
    first = [selector(registry, 42).choose(view)[0].seed_id for _ in range(1)]
    again = [selector(registry, 42).choose(view)[0].seed_id for _ in range(1)]
    assert first == again


def test_choose_reports_applicable_count(registry):
    seed, count = selector(registry).choose(AttackStateView({}, 0))
    assert seed is not None and count == 48


def test_nothing_applicable_returns_none(registry):
    view = AttackStateView({qi: "x, y" for qi in ("AGE", "SEX", "LOC", "OCCP", "EDU", "REL", "INC", "POB")}, 3)
    assert selector(registry).choose(view) == (None, 0)


def test_bind_subject(registry):
    seed = registry.by_id("LOC-S1-O1-001")
    text = selector(registry).bind(seed, AttackStateView({}, 0))
    assert text.startswith("현재까지 제공된 정보를 바탕으로 이 사용자의 현재 거주 지역")
    assert "[" not in text


def test_bind_previously_disclosed_value(registry):
    seed = registry.by_id("LOC-S1-O2-001")
    text = selector(registry).bind(seed, AttackStateView({"LOC": "Switzerland"}, 1))
    assert "'Switzerland'" in text
    assert "[" not in text


def test_bind_without_disclosed_value_fails(registry):
    with pytest.raises(BindingError):
        selector(registry).bind(registry.by_id("LOC-S1-O2-001"), AttackStateView({}, 1))


# ── 노릴 QI 범위 제한 ─────────────────────────────────────

def test_target_qi_scope_keeps_only_seeds_for_those_qis(registry):
    scope = frozenset({"AGE", "LOC", "OCCP", "EDU", "REL"})
    seeds = selector(registry).applicable(AttackStateView({}, prior_turns=0, target_qi_scope=scope))
    assert seeds and {s.target_qi for s in seeds} <= scope
    assert len(seeds) < len(selector(registry).applicable(AttackStateView({}, prior_turns=0)))


def test_empty_scope_leaves_no_seed(registry):
    assert selector(registry).applicable(AttackStateView({}, 0, target_qi_scope=frozenset())) == ()
