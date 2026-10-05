# ontology/model.py · builder.py · isco.py 테스트 — 구조 검사, 계층 모양, 버전 해시

from __future__ import annotations

import pytest

from core.schemas import QI_TYPES
from ontology.builder import ONTOLOGY_VERSION, build_ontology, slug
from ontology.isco import (
    ISCO_LABELS,
    OccupationTableError,
    clean_occupation,
    code_ancestors,
    load_occupation_table,
)
from ontology.model import Node, Ontology, OntologyError, root_id
from tests.orchestrator.sample_data import RAW_PROFILES

ROOTS = [Node(root_id(qi), qi, None, "any", "전체", "any") for qi in QI_TYPES]


# ── 구조 검사 ─────────────────────────────────────────────

def test_rejects_duplicate_missing_parent_and_cross_attribute_parent():
    with pytest.raises(OntologyError, match="중복"):
        Ontology([*ROOTS, ROOTS[0]], version="t")
    with pytest.raises(OntologyError, match="없습니다"):
        Ontology([*ROOTS, Node("AGE:X", "AGE", "AGE:NOPE", "x", "x", "x")], version="t")
    with pytest.raises(OntologyError, match="다른 QI"):
        Ontology([*ROOTS, Node("AGE:X", "AGE", "LOC:ANY", "x", "x", "x")], version="t")


def test_requires_one_root_per_qi():
    with pytest.raises(OntologyError, match="뿌리"):
        Ontology(ROOTS[:-1], version="t")
    with pytest.raises(OntologyError, match="뿌리"):
        Ontology([*ROOTS, Node("AGE:OTHER", "AGE", None, "any", "x", "x")], version="t")


# ── 계층 모양 (D1 Stage 3.1) ──────────────────────────────

@pytest.mark.parametrize(
    "node_id, chain",
    [
        ("AGE:Y28", ("AGE:5Y25", "AGE:10Y20", "AGE:ANY")),                       # 28 → 25–29 → 20–29
        ("LOC:CH:zurich", ("LOC:C:CH", "LOC:ANY")),
        ("POB:DE:berlin", ("POB:C:DE", "POB:ANY")),
        ("OCCP:2512", ("OCCP:251", "OCCP:25", "OCCP:2", "OCCP:ANY")),            # Unit → Minor → Sub-major → Major
        ("OCCP:X", ("OCCP:ANY",)),
        ("EDU:MASTER", ("EDU:TERTIARY", "EDU:ANY")),
        ("EDU:HS", ("EDU:ANY",)),
        ("INC:VERY_HIGH", ("INC:ANY",)),
        ("REL:ENGAGED", ("REL:ANY",)),
        ("SEX:FEMALE", ("SEX:ANY",)),
    ],
)
def test_hierarchy_chains(ontology, node_id, chain):
    assert ontology.ancestors(node_id) == chain


def test_age_bins_are_nested_on_the_same_anchor(ontology):
    for age in range(10, 100):
        five, ten, _ = ontology.ancestors(f"AGE:Y{age}")
        assert five == f"AGE:5Y{age // 5 * 5}" and ten == f"AGE:10Y{age // 10 * 10}"


def test_rel_has_six_flat_categories(ontology):
    categories = [n for n in ontology.nodes("REL") if not n.is_root]
    assert len(categories) == 6 and all(n.parent_id == "REL:ANY" for n in categories)


def test_no_state_level_in_location(ontology):
    assert {n.level for n in ontology.nodes("LOC")} == {"any", "country", "city"}


def test_cities_come_from_the_reference_population_per_attribute(ontology):
    assert "LOC:CH:zurich" in ontology and "POB:CH:zurich" in ontology
    assert "LOC:DE:munich" in ontology
    assert "LOC:FR:paris" not in ontology          # 8명 중 파리에 사는 사람이 없다


# ── 관계 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "existing, candidate, expected",
    [
        ("LOC:C:CH", "LOC:C:CH", "SAME"),
        ("LOC:C:CH", "LOC:CH:zurich", "DESCENDANT"),
        ("LOC:CH:zurich", "LOC:C:CH", "ANCESTOR"),
        ("LOC:CH:zurich", "LOC:CH:geneva", "INCOMPATIBLE"),
        ("AGE:10Y30", "AGE:Y34", "DESCENDANT"),
        ("AGE:5Y30", "AGE:5Y35", "INCOMPATIBLE"),
        ("EDU:TERTIARY", "EDU:HS", "INCOMPATIBLE"),
    ],
)
def test_relation(ontology, existing, candidate, expected):
    assert ontology.relation(existing, candidate) == expected


def test_relation_across_attributes_is_an_error(ontology):
    with pytest.raises(OntologyError):
        ontology.relation("LOC:C:CH", "POB:C:CH")


def test_most_specific(ontology):
    assert ontology.most_specific(["AGE:10Y30", "AGE:Y34", "AGE:5Y30"]) == "AGE:Y34"
    assert ontology.most_specific(["AGE:10Y30", "AGE:10Y20"]) is None


# ── 버전 · 재현성 ─────────────────────────────────────────

def test_same_input_gives_same_hash_regardless_of_order():
    profiles = list(RAW_PROFILES.values())
    first, second = build_ontology(profiles), build_ontology(list(reversed(profiles)))
    assert first.content_hash == second.content_hash
    assert first.version == ONTOLOGY_VERSION and set(first.sources) >= {"occupation_alias", "countries", "cities"}


def test_hash_changes_when_city_list_changes():
    profiles = list(RAW_PROFILES.values())
    extra = {**profiles[0], "city_country": "Paris, France"}
    assert build_ontology(profiles).content_hash != build_ontology([*profiles, extra]).content_hash


def test_slug_handles_accents_and_spaces():
    assert slug("São Paulo") == "sao_paulo" and slug(" New  York ") == "new_york"


# ── 직업 대응표 ───────────────────────────────────────────

def test_occupation_table_is_consistent_with_isco_labels():
    table = load_occupation_table()
    assert len(table.aliases) >= 170
    for alias in table.aliases.values():
        assert alias.isco_code == "X" or alias.isco_code in ISCO_LABELS
        assert all(code in ISCO_LABELS for code in code_ancestors(alias.isco_code))


@pytest.mark.parametrize(
    "text, expected",
    [("Retired University Lecturer", "university lecturer"), ("a part-time barista.", "barista"),
     ("Senior Engineer", "engineer"), ("retired part-time tutor", "tutor")],
)
def test_clean_occupation(text, expected):
    assert clean_occupation(text) == expected


def test_code_ancestors():
    assert code_ancestors("2512") == ("251", "25", "2") and code_ancestors("X") == ()


def test_occupation_table_rejects_unknown_code(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("alias,isco_code,confidence,note\nastronaut,9999,H,\n", encoding="utf-8")
    with pytest.raises(OccupationTableError, match="이름이 없는 코드"):
        load_occupation_table(path)
