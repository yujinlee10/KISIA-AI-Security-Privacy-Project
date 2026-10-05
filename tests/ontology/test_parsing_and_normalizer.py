# ontology/parsing.py · normalizer.py 테스트 — 표현 → node, 모호·미해석 처리, 이름표 되읽기

from __future__ import annotations

import pytest

from normalizer.normalizer import MappingReason, MappingStatus
from ontology.normalizer import OntologyNormalizer
from tests.normalizer.sample_data import raw


def one(parser, attribute, *texts):
    return parser.parse(attribute, *texts).node_id


# ── AGE ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "texts, expected",
    [
        (("34세",), "AGE:Y34"),
        (("서른넷", "34"), "AGE:Y34"),
        (("34 years old",), "AGE:Y34"),
        (("a 34-year-old",), "AGE:Y34"),
        (("30대",), "AGE:10Y30"),
        (("30대 초반", "early 30s"), "AGE:5Y30"),
        (("30대 후반", "late thirties"), "AGE:5Y35"),
        (("30대 중반", "mid-30s"), "AGE:10Y30"),          # 33~37은 5년 구간 둘에 걸친다 → 10년 구간까지만
        (("25-29세",), "AGE:5Y25"),
        (("20~29",), "AGE:10Y20"),
        (("34세로 30대", "34"), "AGE:Y34"),               # 한 줄기에 있으면 가장 구체적인 값
        (("10대", "teenager"), "AGE:10Y10"),
    ],
)
def test_age(parser, texts, expected):
    assert one(parser, "AGE", *texts) == expected


@pytest.mark.parametrize("texts", [("20~30대",), ("28 to 33",)])
def test_age_spanning_two_decades_is_ambiguous(parser, texts):
    parsed = parser.parse("AGE", *texts)
    assert parsed.node_id is None and parsed.node_ids == ("AGE:10Y20", "AGE:10Y30")


@pytest.mark.parametrize(
    "texts, expected",
    [
        (("서른네 살",), "AGE:Y34"),
        (("서른 살",), "AGE:Y30"),
        (("삼십대 후반",), "AGE:5Y35"),
        (("서른 초반",), "AGE:5Y30"),
        (("age: 34",), "AGE:Y34"),
        (("late teens",), "AGE:5Y15"),
    ],
)
def test_age_korean_numerals_and_other_forms(parser, texts, expected):
    assert one(parser, "AGE", *texts) == expected


@pytest.mark.parametrize(
    "texts",
    [("성인", "adult"), ("젊은 편",), ("150세",),
     ("60세 이상", "over 60"), ("30세 미만", "under 30"), ("30대 이상",), ("65+",), ("20세기",)],
)
def test_age_without_a_node(parser, texts):
    """한쪽이 열린 범위는 맞는 node가 없다. 경계 숫자를 정확한 나이로 읽으면 안 된다."""
    assert parser.parse("AGE", *texts).node_ids == ()


# ── LOC · POB ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "attribute, texts, expected, reason",
    [
        ("LOC", ("취리히", "Zurich"), "LOC:CH:zurich", "CITY_MATCH"),
        ("LOC", ("Zurich, Switzerland",), "LOC:CH:zurich", "EXACT_MATCH"),
        ("LOC", ("스위스",), "LOC:C:CH", "COUNTRY_MATCH"),
        ("LOC", ("미국", "USA"), "LOC:C:US", "COUNTRY_MATCH"),
        ("LOC", ("United States",), "LOC:C:US", "COUNTRY_MATCH"),           # USA와 같은 node
        ("LOC", ("Lausanne, Switzerland",), "LOC:C:CH", "COUNTRY_MATCH"),   # 목록에 없는 도시 → 국가까지만
        ("POB", ("베를린", "Berlin, Germany"), "POB:DE:berlin", "EXACT_MATCH"),
        ("POB", ("Columbus, Ohio",), "POB:C:US", "COUNTRY_MATCH"),          # 주 이름은 국가로만 연결
    ],
)
def test_geo(parser, attribute, texts, expected, reason):
    parsed = parser.parse(attribute, *texts)
    assert (parsed.node_id, parsed.reason) == (expected, reason)


@pytest.mark.parametrize(
    "texts, expected",
    [
        (("the United States",), "LOC:C:US"),
        (("England",), "LOC:C:GB"),
        (("Austin, Texas",), "LOC:C:US"),                       # 주 이름 → 국가 (도시는 목록에 없음)
        (("Edinburgh, Scotland",), "LOC:C:GB"),
        (("스위스 (Switzerland)",), "LOC:C:CH"),                 # '한국어 (영어)'로 묶인 표현
    ],
)
def test_subdivisions_articles_and_paired_phrases(parser, texts, expected):
    assert one(parser, "LOC", *texts) == expected


def test_unknown_region_is_not_resolved_from_city_name_alone(parser):
    """'Zurich, Ontario'의 Zurich를 스위스 취리히로 읽으면 안 된다."""
    assert parser.parse("LOC", "Zurich, Ontario").node_ids == ()


def test_city_and_country_expressions_resolve_to_the_more_specific(parser):
    assert one(parser, "LOC", "스위스", "Zurich") == "LOC:CH:zurich"


def test_unknown_place(parser):
    parsed = parser.parse("LOC", "아틀란티스", "Atlantis")
    assert (parsed.node_ids, parsed.reason) == ((), "NOT_IN_REFERENCE")


# ── OCCP ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "texts, expected",
    [
        (("소프트웨어 엔지니어", "software engineer"), "OCCP:2512"),
        (("Retired Nurse",), "OCCP:2221"),
        (("교사", "teacher"), "OCCP:23"),                                    # 학교급 불명 → 2자리까지만
        (("은퇴", "retired"), "OCCP:X"),
        (("정보통신기술 전문가",), "OCCP:25"),                                 # ISCO 그룹 이름 (일반화된 표현)
        (("x", "Software and applications developers and analysts"), "OCCP:251"),
        (("전문직 (ISCO 2)",), "OCCP:2"),
    ],
)
def test_occupation(parser, texts, expected):
    assert one(parser, "OCCP", *texts) == expected


def test_unknown_occupation_is_not_guessed(parser):
    assert parser.parse("OCCP", "우주비행사", "astronaut").node_ids == ()


# ── EDU · INC · REL · SEX ─────────────────────────────────

@pytest.mark.parametrize(
    "attribute, texts, expected",
    [
        ("EDU", ("석사", "Master's degree"), "EDU:MASTER"),
        ("EDU", ("Masters in Computer Science",), "EDU:MASTER"),
        ("EDU", ("MBA in Finance",), "EDU:MASTER"),
        ("EDU", ("PhD in Physics",), "EDU:DOCTORATE"),
        ("EDU", ("Bachelor's in Civil Engineering",), "EDU:COLLEGE"),
        ("EDU", ("High School Diploma",), "EDU:HS"),
        ("EDU", ("studying towards a Bachelors in Economics",), "EDU:IN_COLLEGE"),
        ("EDU", ("studying towards a Masters in Literature",), "EDU:COLLEGE"),     # 학사는 마친 것으로 본다
        ("EDU", ("studying towards a PhD in Physics",), "EDU:TERTIARY"),
        ("EDU", ("Law Degree",), "EDU:TERTIARY"),
        ("EDU", ("대학 이상의 학력", "tertiary education"), "EDU:TERTIARY"),
        ("INC", ("매우 높은 소득", "very high"), "INC:VERY_HIGH"),
        ("INC", ("고소득", "high"), "INC:HIGH"),
        ("INC", ("medium",), "INC:MIDDLE"),                                        # middle이 표준, medium은 다른 표기
        ("INC", ("저소득",), "INC:LOW"),
        ("REL", ("미혼", "single"), "REL:SINGLE"),
        ("REL", ("in relationship",), "REL:IN_RELATIONSHIP"),                      # 기준 집단의 표기 갈림
        ("REL", ("in a relationship",), "REL:IN_RELATIONSHIP"),
        ("REL", ("약혼", "engaged"), "REL:ENGAGED"),
        ("SEX", ("여성", "female"), "SEX:FEMALE"),
        ("SEX", ("남성", "male"), "SEX:MALE"),
    ],
)
def test_categorical(parser, attribute, texts, expected):
    assert one(parser, attribute, *texts) == expected


# ── 부정·비교·과정 중 표현 (독립 검토에서 나온 오분류 사례) ──

@pytest.mark.parametrize(
    "texts, expected",
    [
        (("never married",), "REL:SINGLE"),
        (("not married",), "REL:SINGLE"),
        (("결혼을 하지 않았다",), "REL:SINGLE"),
        (("결혼한 적 없음",), "REL:SINGLE"),
        (("남편과 사별",), "REL:WIDOWED"),
        (("husband passed away",), "REL:WIDOWED"),
        (("결혼했다", "married"), "REL:MARRIED"),
    ],
)
def test_relationship_negation_and_bereavement(parser, texts, expected):
    assert one(parser, "REL", *texts) == expected


@pytest.mark.parametrize("texts", [("배우자 없음",), ("no spouse",), ("ex-wife",), ("전 남편",), ("결혼 예정",),
                                   ("연애 안 함",), ("남자친구 없음",)])
def test_relationship_expressions_without_a_category(parser, texts):
    """배우자가 없다는 말만으로는 미혼·이혼·사별 중 무엇인지 알 수 없다. 기혼·연애 중으로 읽으면 안 된다."""
    assert parser.parse("REL", *texts).node_ids == ()


@pytest.mark.parametrize(
    "texts, expected",
    [
        (("undergraduate student",), "EDU:IN_COLLEGE"),
        (("graduate student",), "EDU:COLLEGE"),
        (("박사과정",), "EDU:TERTIARY"),
        (("PhD candidate",), "EDU:TERTIARY"),
        (("석사과정",), "EDU:COLLEGE"),
        (("석사 과정을 마쳤다",), "EDU:MASTER"),
        (("전문학사",), "EDU:TERTIARY"),
        (("HS diploma",), "EDU:HS"),
        (("college graduate",), "EDU:COLLEGE"),
        (("BSc in Physics",), "EDU:COLLEGE"),
        (("MSc",), "EDU:MASTER"),
        (("Doctorate in Medicine",), "EDU:TERTIARY"),          # MD와 같은 node
        (("MD in Medicine",), "EDU:TERTIARY"),
        (("Doctorate in Physics",), "EDU:DOCTORATE"),
    ],
)
def test_education_enrollment_and_degree_names(parser, texts, expected):
    assert one(parser, "EDU", *texts) == expected


@pytest.mark.parametrize("texts", [("no degree",), ("학위 없음",), ("some college, no degree",),
                                   ("고등학교 중퇴",), ("high school dropout",), ("high school student",)])
def test_education_without_a_node(parser, texts):
    assert parser.parse("EDU", *texts).node_ids == ()


@pytest.mark.parametrize("texts", [("not high",), ("소득이 높지 않다",), ("not very high",),
                                   ("below average",), ("평균 이하",), ("above average",)])
def test_income_negation_and_comparison_are_not_levels(parser, texts):
    assert parser.parse("INC", *texts).node_ids == ()


@pytest.mark.parametrize("texts, expected", [(("높음",), "INC:HIGH"), (("낮은 편",), "INC:LOW"),
                                             (("매우 높음",), "INC:VERY_HIGH")])
def test_income_plain_korean(parser, texts, expected):
    assert one(parser, "INC", *texts) == expected


def test_salary_amount_is_not_converted_to_a_level(parser):
    assert parser.parse("INC", "연 6만 달러", "60 thousand us dollars").node_ids == ()


def test_conflicting_relationship_words_are_ambiguous(parser):
    parsed = parser.parse("REL", "이혼 후 재혼", "divorced, now married")
    assert parsed.node_id is None and set(parsed.node_ids) == {"REL:DIVORCED", "REL:MARRIED"}


# ── 결정성 · 이름표 되읽기 ─────────────────────────────────

def test_parsing_is_deterministic(parser):
    inputs = [("AGE", "30대 초반"), ("LOC", "Zurich"), ("OCCP", "nurse"), ("EDU", "석사"), ("REL", "미혼")]
    assert [parser.parse(a, t) for a, t in inputs] == [parser.parse(a, t) for a, t in inputs]


def test_node_labels_read_back_as_the_same_population(index, ontology, parser):
    """
    Defender가 일반화한 값은 node의 이름표로 응답에 쓰이고, 고쳐 쓴 응답에서 다시 추출·정규화된다.
    이름표를 다시 해석했을 때 같은 node(이름이 같은 ISCO 상·하위 그룹이면 같은 사람들)가 나와야 한다.
    """
    for node in ontology.nodes():
        if node.is_root or node.level in ("city", "exact"):
            continue          # 도시·정확한 나이는 일반화의 목적지가 되지 않는다 (아래 별도 확인)
        parsed = parser.parse(node.attribute, node.label_ko, node.label_en)
        assert parsed.node_id is not None, node.node_id
        assert ontology.is_same_or_descendant(node.node_id, parsed.node_id), node.node_id
        assert index.node_count(parsed.node_id) == index.node_count(node.node_id), node.node_id


def test_exact_age_and_city_labels_read_back(ontology, parser):
    assert one(parser, "AGE", ontology.node("AGE:Y34").label_ko, ontology.node("AGE:Y34").label_en) == "AGE:Y34"
    assert one(parser, "LOC", ontology.node("LOC:CH:zurich").label_en) == "LOC:CH:zurich"


# ── OntologyNormalizer ────────────────────────────────────

def test_normalizer_statuses(ontology, parser):
    normalizer = OntologyNormalizer(ontology, parser)
    mapped, pending, unmapped, sex = normalizer.normalize_all([
        raw("LOC", "취리히", "Zurich", observation_id="a"),
        raw("AGE", "20~30대", None, observation_id="b"),
        raw("OCCP", "우주비행사", "astronaut", observation_id="c"),
        raw("SEX", "여성", "female", observation_id="d"),
    ])

    assert (mapped.status, mapped.canonical_value, mapped.reason) == (
        MappingStatus.MAPPED, "LOC:CH:zurich", MappingReason.CITY_MATCH)
    assert (pending.status, pending.candidates) == (MappingStatus.PENDING, ("AGE:10Y20", "AGE:10Y30"))
    assert (unmapped.status, unmapped.reason) == (MappingStatus.UNMAPPED, MappingReason.NOT_IN_REFERENCE)
    assert (sex.status, sex.canonical_value) == (MappingStatus.MAPPED, "SEX:FEMALE")     # 8개 QI 모두 대상
    assert [o.observation_id for o in (mapped, pending, unmapped, sex)] == ["a", "b", "c", "d"]


# ── 국가이기도 하고 주(州)이기도 한 이름, 도시 표기 갈림 ──

@pytest.fixture(scope="module")
def georgia_parser():
    from ontology.index import PopulationIndex
    from tests.orchestrator.sample_data import RAW_PROFILES
    base = RAW_PROFILES["p1"]
    profiles = [*RAW_PROFILES.values(),
                {**base, "city_country": "Atlanta, United States"},
                {**base, "city_country": "Tbilisi, Georgia"},
                {**base, "city_country": "Bejiing, China"},
                {**base, "city_country": "Beijing, China"}]
    return PopulationIndex.build(profiles)


def test_georgia_is_resolved_by_where_the_city_is(georgia_parser):
    parser = georgia_parser.parser
    assert parser.parse("LOC", "Atlanta, Georgia").node_id == "LOC:US:atlanta"
    assert parser.parse("LOC", "Tbilisi, Georgia").node_id == "LOC:GE:tbilisi"
    assert parser.parse("LOC", "Georgia").node_id == "LOC:C:GE"                      # 단독이면 국가로 읽는다
    unknown = parser.parse("LOC", "Savannah, Georgia")                               # 어느 쪽인지 알 수 없다
    assert unknown.node_id is None and unknown.node_ids == ("LOC:C:GE", "LOC:C:US")


def test_city_spelling_variants_share_one_node(georgia_parser):
    assert georgia_parser.node_count("LOC:CN:beijing") == 2
    assert georgia_parser.parser.parse("LOC", "Bejiing, China").node_id == "LOC:CN:beijing"
