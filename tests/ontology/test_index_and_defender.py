# ontology/mapper.py · index.py · defender.py 테스트 — 기준 집단 매핑, 후보 수 k, 일반화 탐색
# 사람 수는 conftest.py의 8명 표로 손 계산한 값이다.

from __future__ import annotations

import pytest

from ontology.defender import DeltaItem, plan_release
from ontology.index import PopulationIndex
from risk_defender.demo import DEFAULT_DATA_PATH
from tests.orchestrator.sample_data import RAW_PROFILES


# ── 기준 집단 매핑 (P_ref 정규화) ─────────────────────────

def test_reference_population_is_fully_mapped(index):
    report = index.report
    assert report.population_size == 8 and report.fully_mapped
    assert all(item.total == item.mapped == 8 for item in report.attributes.values())
    assert report.attributes["OCCP"].by_level == {"unit": 7, "sub_major": 1}     # teacher는 2자리까지만
    assert report.attributes["AGE"].by_level == {"exact": 8}


def test_unmapped_values_are_reported_not_dropped():
    profiles = [*RAW_PROFILES.values(), {**RAW_PROFILES["p1"], "occupation": "astronaut", "education": "???"}]
    report = PopulationIndex.build(profiles).report

    assert not report.fully_mapped
    assert report.attributes["OCCP"].unmapped_values == {"astronaut": 1}
    assert report.attributes["EDU"].unmapped_values == {"???": 1}
    assert report.to_log_dict()["attributes"]["OCCP"]["coverage"] == round(8 / 9, 6)


@pytest.mark.skipif(not DEFAULT_DATA_PATH.exists(), reason="기준 집단 데이터 미준비 (python -m adapters.prepare_reference)")
def test_real_300_profiles_are_fully_mapped():
    """D1: 300 profile을 Main 전에 100% 정규화."""
    import json
    profiles = list(json.loads(DEFAULT_DATA_PATH.read_text(encoding="utf-8")).values())
    index = PopulationIndex.build(profiles)

    assert index.size == 300 and index.report.fully_mapped
    assert index.node_count("LOC:C:US") == 21          # "United States" 18명 + "USA" 3명이 한 node로 모인다
    beijing = sum(1 for profile in profiles if profile["city_country"] in ("Beijing, China", "Bejiing, China"))
    assert index.node_count("LOC:CN:beijing") == beijing > 0      # 오타 표기 "Bejiing"도 같은 node로 모인다
    assert "LOC:CN:bejiing" not in index.ontology
    assert index.node_count("REL:IN_RELATIONSHIP") == 37   # "in a relationship" 30명 + "in relationship" 7명


# ── 후보 수 k ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "constraints, expected",
    [
        ({}, 8),
        ({"LOC": "LOC:C:CH"}, 5),
        ({"LOC": "LOC:CH:zurich"}, 3),
        ({"AGE": "AGE:10Y30"}, 5),
        ({"AGE": "AGE:5Y30"}, 3),
        ({"AGE": "AGE:Y34"}, 1),
        ({"OCCP": "OCCP:2512"}, 4),
        ({"OCCP": "OCCP:2"}, 7),                                  # 전문가: 요리사(3434)만 빠진다
        ({"EDU": "EDU:TERTIARY"}, 7),
        ({"SEX": "SEX:MALE", "INC": "INC:HIGH"}, 2),              # risk_defender 백엔드가 세지 않던 QI
        ({"POB": "POB:C:DE", "LOC": "LOC:C:CH"}, 2),
        ({"LOC": "LOC:C:CH", "AGE": "AGE:10Y30", "OCCP": "OCCP:2512"}, 2),
        ({"OCCP": "OCCP:5132"}, 0),                               # 바리스타는 8명 중에 없다
        ({"LOC": "LOC:ANY"}, 8),                                  # 뿌리는 조건이 아니다
    ],
)
def test_count(index, constraints, expected):
    assert index.count(constraints) == expected


def test_parent_count_is_never_smaller_than_child(index, ontology):
    """D1 검증 규칙: k(parent) >= k(child). 다른 QI 조건이 함께 걸려 있어도 성립해야 한다."""
    others = [{}, {"SEX": "SEX:MALE"}, {"EDU": "EDU:TERTIARY", "REL": "REL:SINGLE"}]
    for node in ontology.nodes():
        if node.parent_id is None or ontology.node(node.parent_id).is_root:
            continue
        for extra in others:
            if node.attribute in extra:
                continue
            child = index.count({**extra, node.attribute: node.node_id})
            parent = index.count({**extra, node.attribute: node.parent_id})
            assert parent >= child, (node.node_id, extra)


def test_children_partition_their_parent(index, ontology):
    for parent in ("AGE:10Y30", "LOC:C:CH", "EDU:TERTIARY", "OCCP:25"):
        assert sum(index.node_count(child) for child in ontology.children(parent)) == index.node_count(parent)


# ── 일반화 탐색 (D1 Stage 11) ─────────────────────────────

def plan(index, state, *delta, k_req=4):
    return plan_release(index, state, [DeltaItem(f"o{i}", node.split(":")[0], node)
                                       for i, node in enumerate(delta, start=1)], k_req=k_req)


def released(result):
    return {item.attribute: item.released_node for item in result.items}


def test_safe_disclosure_is_left_alone(index):
    result = plan(index, {}, "LOC:C:CH")
    assert not result.intervened and (result.k_raw, result.k_planned, result.k_floor) == (5, 5, 4)
    assert result.reason is None


def test_city_is_generalized_to_country(index):
    """취리히 3명 < 하한 4 → 한 단계 위 스위스(5명)."""
    result = plan(index, {}, "LOC:CH:zurich")
    assert released(result) == {"LOC": "LOC:C:CH"}
    assert (result.k_raw, result.k_planned, result.frontier_depth, result.reason) == (3, 5, 1, "BELOW_FLOOR")


def test_search_climbs_one_parent_at_a_time(index):
    """State 스위스(5). 34세 → 30–34(3명) 부족 → 30대(4명)에서 멈춘다."""
    result = plan(index, {"LOC": "LOC:C:CH"}, "AGE:Y34")
    assert released(result) == {"AGE": "AGE:10Y30"}
    assert (result.k_raw, result.k_planned, result.frontier_depth) == (1, 4, 2)
    assert result.information_loss == round(2 / 3, 6)


def test_suppression_is_the_last_resort(index):
    """State 스위스+30대(4). 직업은 2512 → 251 → 25(2명) → 2(3명) 모두 부족 → 억제."""
    result = plan(index, {"LOC": "LOC:C:CH", "AGE": "AGE:10Y30"}, "OCCP:2512")
    item = result.items[0]
    assert item.suppressed and (item.steps, item.max_steps) == (4, 4)
    assert result.k_planned == result.k_before == 4


def test_root_is_never_released(index):
    """부모가 ANY뿐인 값(성별)은 일반화 단계가 없다. 올리면 곧 억제다."""
    result = plan(index, {"LOC": "LOC:CH:zurich"}, "SEX:MALE")          # 취리히 남성 2명 < 하한 3
    assert result.items[0].suppressed and result.items[0].max_steps == 1


def test_earliest_safe_frontier_wins_across_qis(index):
    """취리히+개발자 = 1명. 한 단계 올린 조합은 모두 부족하고, 두 단계에서 '거주지 억제 + 직업 그대로'(4명)가 처음 안전하다."""
    result = plan(index, {}, "LOC:CH:zurich", "OCCP:2512")
    assert released(result) == {"LOC": None, "OCCP": "OCCP:2512"}
    assert (result.frontier_depth, result.k_planned) == (2, 4)


def test_tie_is_broken_by_qi_order(index):
    """스위스 거주(5) + 독일 출생(5) = 2명. 어느 쪽을 억제해도 5명이고 손실도 같다 → QI 순서상 뒤쪽(POB)을 억제."""
    result = plan(index, {}, "LOC:C:CH", "POB:C:DE")
    assert released(result) == {"LOC": "LOC:C:CH", "POB": None}


def test_refinement_stops_at_the_already_disclosed_node(index):
    """State 스위스. 취리히(3명)는 부족하고, 그 위는 이미 공개된 스위스라 새 정보가 아니다 → 억제."""
    result = plan(index, {"LOC": "LOC:C:CH"}, "LOC:CH:zurich")
    assert result.items[0].suppressed and result.items[0].max_steps == 1
    assert result.k_planned == 5


def test_preexisting_risk_uses_current_k_as_floor(index):
    """이미 3명(취리히)이면 하한은 3이다. 더 좁히지 않는 공개(30대: 3명 그대로)는 허용된다 (D1: 전부 차단 금지)."""
    result = plan(index, {"LOC": "LOC:CH:zurich"}, "AGE:10Y30")
    assert result.k_floor == 3 and not result.intervened and result.k_planned == 3


def test_new_zero_match_is_blocked(index):
    """기준 집단에 없는 값(바리스타)을 그대로 내면 0명이 된다 → 공개하지 않는다."""
    result = plan(index, {}, "OCCP:5132")
    assert (result.k_raw, result.reason) == (0, "NEW_ZERO_MATCH")
    assert result.items[0].suppressed and result.k_planned == 8


@pytest.mark.parametrize("leaf", ["AGE:Y34", "OCCP:2512", "EDU:MASTER", "POB:DE:berlin"])
def test_generalization_never_decreases_k(index, ontology, leaf):
    """State가 있을 때도, 한 단계씩 올릴수록 k는 줄지 않는다 (D1: 일반화 후 k 비감소 확인)."""
    attribute = leaf.split(":")[0]
    chain = [leaf, *[a for a in ontology.ancestors(leaf) if not ontology.node(a).is_root]]
    counts = [index.count({"LOC": "LOC:C:CH", attribute: node}) for node in chain]
    assert counts == sorted(counts)


def test_one_observation_per_qi(index):
    with pytest.raises(ValueError):
        plan(index, {}, "AGE:Y34", "AGE:10Y30")


@pytest.mark.parametrize("node", ["LOC:C:CH", "LOC:C:DE", "LOC:CH:geneva"])
def test_delta_must_be_new_information(index, node):
    """이미 공개된 값과 같거나, 더 넓거나, 양립하지 않는 값은 새 공개분이 아니다. 호출 측이 걸러야 한다."""
    state = {"LOC": "LOC:CH:zurich"} if node != "LOC:C:CH" else {"LOC": "LOC:C:CH"}
    with pytest.raises(ValueError, match="새 공개분이 아닙니다"):
        plan(index, state, node)


def test_plan_does_not_change_state(index):
    state = {"LOC": "LOC:C:CH"}
    plan(index, state, "AGE:Y34")
    assert state == {"LOC": "LOC:C:CH"}
