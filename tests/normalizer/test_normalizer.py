# normalizer/vocabulary.py, normalizer/normalizer.py 단위 테스트

from __future__ import annotations

import pytest

from normalizer.normalizer import MappingReason, MappingStatus, Normalizer
from normalizer.vocabulary import ReferenceVocabulary, country_key
from tests.normalizer.sample_data import SAMPLE_RECORDS, raw


@pytest.fixture
def vocab():
    return ReferenceVocabulary.from_population(SAMPLE_RECORDS)


@pytest.fixture
def normalizer(vocab):
    return Normalizer(vocab)


# ── vocabulary ────────────────────────────────────────────

def test_vocabulary_counts_and_case_insensitive_find(vocab):
    assert vocab.population_size == 7
    assert vocab.count("OCCP", "software engineer") == 2
    assert vocab.find("OCCP", "  Software   ENGINEER ") == ("software engineer",)


def test_country_aliases_share_a_key():
    assert country_key("USA") == country_key("United States") == "united states"
    assert country_key("UAE") == "united arab emirates"


def test_city_index_groups_country_spellings(vocab):
    entries = dict()
    for ckey, full in vocab.loc_by_city["new york"]:
        entries.setdefault(ckey, []).append(full)
    assert set(entries) == {"united states"}
    assert sorted(entries["united states"]) == ["New York, USA", "New York, United States"]


# ── 범위 밖 QI ────────────────────────────────────────────

@pytest.mark.parametrize("attribute", ["SEX", "INC", "POB"])
def test_out_of_risk_scope(normalizer, attribute):
    result = normalizer.normalize(raw(attribute, "값"))
    assert result.status is MappingStatus.UNMAPPED
    assert result.reason is MappingReason.OUT_OF_RISK_SCOPE


# ── AGE · EDU · REL ───────────────────────────────────────

def test_age_mapped(normalizer):
    result = normalizer.normalize(raw("AGE", "30대 중반", "mid-30s"))
    assert (result.status, result.canonical_value) == (MappingStatus.MAPPED, "30s")


def test_age_ambiguous_is_pending(normalizer):
    result = normalizer.normalize(raw("AGE", "30대 후반에서 40대 초반"))
    assert result.status is MappingStatus.PENDING
    assert result.candidates == ("30s", "40s")


def test_age_not_in_reference_is_unmapped(normalizer):
    """60s는 표본 기준 집단에 없다. MAPPED로 넘기면 k=0이 된다."""
    result = normalizer.normalize(raw("AGE", "60대"))
    assert result.status is MappingStatus.UNMAPPED
    assert result.reason is MappingReason.NOT_IN_REFERENCE


def test_age_generalized_node(normalizer):
    result = normalizer.normalize(raw("AGE", "성인"))
    assert (result.status, result.reason, result.canonical_value) == \
        (MappingStatus.MAPPED, MappingReason.GENERALIZED_NODE, "adult")


def test_student_is_unmapped_when_reference_has_no_students(normalizer):
    """기준 집단에 In College/HS Diploma가 0명이면 추측해서 다른 값으로 바꾸지 않는다."""
    result = normalizer.normalize(raw("EDU", "대학생", "college student"))
    assert result.status is MappingStatus.UNMAPPED
    assert result.reason is MappingReason.NOT_IN_REFERENCE


def test_rel_spelling_split_picks_majority_and_records_other(normalizer):
    result = normalizer.normalize(raw("REL", "연애 중", "in a relationship"))
    assert result.canonical_value == "in a relationship"
    assert result.alternatives == ("in relationship",)


def test_no_rule_is_unmapped(normalizer):
    result = normalizer.normalize(raw("REL", "복잡함", "complicated"))
    assert (result.status, result.reason) == (MappingStatus.UNMAPPED, MappingReason.NO_RULE)


# ── OCCP: 그대로 일치할 때만 ───────────────────────────────

def test_occp_exact_match_via_value_en(normalizer):
    result = normalizer.normalize(raw("OCCP", "소프트웨어 엔지니어", "a Software Engineer"))
    assert (result.status, result.canonical_value) == (MappingStatus.MAPPED, "software engineer")


def test_occp_similar_but_different_is_not_guessed(normalizer):
    result = normalizer.normalize(raw("OCCP", "소프트웨어 개발자", "software developer"))
    assert result.status is MappingStatus.UNMAPPED


# ── LOC ───────────────────────────────────────────────────

def test_loc_city_only(normalizer):
    result = normalizer.normalize(raw("LOC", "취리히", "Zurich"))
    assert (result.status, result.reason, result.canonical_value) == \
        (MappingStatus.MAPPED, MappingReason.CITY_MATCH, "Zurich, Switzerland")


def test_loc_city_with_country_alias(normalizer):
    result = normalizer.normalize(raw("LOC", "뉴욕", "New York, USA"))
    assert result.status is MappingStatus.MAPPED
    assert result.canonical_value == "New York, United States"     # 많이 쓰인 표기
    assert result.alternatives == ("New York, USA",)              # 빠진 표기 기록


def test_loc_same_city_name_in_two_countries_is_pending(normalizer):
    result = normalizer.normalize(raw("LOC", "파리", "Paris"))
    assert result.status is MappingStatus.PENDING
    assert set(result.candidates) == {"Paris, France", "Paris, United States"}


def test_loc_city_disambiguated_by_country(normalizer):
    result = normalizer.normalize(raw("LOC", "파리", "Paris, France"))
    assert result.canonical_value == "Paris, France"


def test_loc_country_only(normalizer):
    result = normalizer.normalize(raw("LOC", "스위스", "Switzerland"))
    assert (result.reason, result.canonical_value) == (MappingReason.COUNTRY_MATCH, "Switzerland")


def test_loc_country_alias_spelling(normalizer):
    result = normalizer.normalize(raw("LOC", "미국", "USA"))
    assert result.canonical_value == "United States"
    assert result.alternatives == ("USA",)


def test_loc_unknown_city_falls_back_to_country(normalizer):
    result = normalizer.normalize(raw("LOC", "바젤", "Basel, Switzerland"))
    assert (result.reason, result.canonical_value) == (MappingReason.COUNTRY_MATCH, "Switzerland")


def test_loc_unknown_is_unmapped(normalizer):
    result = normalizer.normalize(raw("LOC", "서울", "Seoul"))
    assert result.status is MappingStatus.UNMAPPED


# ── 공통 ──────────────────────────────────────────────────

def test_normalize_all_keeps_order_and_ids(normalizer):
    results = normalizer.normalize_all([
        raw("LOC", "취리히", "Zurich", observation_id="t2-o1"),
        raw("SEX", "남성", "male", observation_id="t2-o2"),
    ])
    assert [r.observation_id for r in results] == ["t2-o1", "t2-o2"]
    log = results[0].to_log_dict()
    assert log["status"] == "MAPPED" and log["canonical_value"] == "Zurich, Switzerland"
