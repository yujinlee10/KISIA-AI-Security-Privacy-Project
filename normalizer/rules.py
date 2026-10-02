# QI별 표현 → 후보 값 규칙 (한국어 raw_value와 영어 value_en을 함께 본다)
#
# 각 함수는 "이 표현이 가리킬 수 있는 risk_defender 값들의 집합"을 돌려준다.
#   후보 0개 → 규칙으로 해석 못 함 (UNMAPPED)
#   후보 1개 → 해석됨 (기준 집단에 있는지는 normalizer.py가 확인)
#   후보 2개 이상 → 모호함 (PENDING)
# 판정은 normalizer.py가 하고, 이 파일은 해석만 한다.
#
# 규칙에 들어간 값 형식은 risk_defender(이유진 파트)를 따른다.
#   AGE  : "20s", "30s" ... (10년 단위), "teens"            ← risk_defender.schema.to_age_band
#   EDU  : "PhD", "Master's", "College Degree", "HS Diploma", "In College/HS Diploma"
#                                                             ← risk_defender.loader.derive_education
#   REL  : "single", "married", "engaged", "in a relationship", "divorced", "widowed"
#   일반화 노드 : AGE adult/senior/young, EDU higher_education/non_advanced_degree,
#                REL partnered/not_partnered                 ← risk_defender.risk._matches_qi
#
# LOC·OCCP는 규칙이 아니라 기준 집단 어휘와 직접 대조한다 (normalizer.py).

from __future__ import annotations

import re

from risk_defender.schema import to_age_band


# ──────────────────────────────────────────────────────────────
# risk_defender가 이해하는 일반화 노드 (risk_defender/risk.py의 _matches_qi와 같은 목록)
# 기준 집단 값 목록에는 없지만 후보 계산에 쓸 수 있는 값이다.
# ──────────────────────────────────────────────────────────────

GENERALIZED_NODES: dict[str, frozenset[str]] = {
    "AGE": frozenset({"adult", "senior", "young"}),
    "EDU": frozenset({"higher_education", "non_advanced_degree"}),
    "REL": frozenset({"partnered", "not_partnered"}),
}


def _join(*texts: str | None) -> str:
    return " ".join(t for t in texts if t).lower()


# ──────────────────────────────────────────────────────────────
# AGE
# ──────────────────────────────────────────────────────────────

_ENGLISH_DECADE_WORDS = {
    "twenties": 20, "thirties": 30, "forties": 40, "fifties": 50,
    "sixties": 60, "seventies": 70, "eighties": 80, "nineties": 90,
}

_AGE_PATTERNS = (
    # (패턴, 값을 만드는 방법)
    (re.compile(r"(\d)0\s*대"), lambda m: f"{m.group(1)}0s" if m.group(1) != "1" else "teens"),  # 30대
    (re.compile(r"\b(\d)0'?s\b"), lambda m: f"{m.group(1)}0s" if m.group(1) != "1" else "teens"),  # 30s
    (re.compile(r"(\d{1,3})\s*(?:세|살)"), lambda m: to_age_band(int(m.group(1)))),                # 34세
    (re.compile(r"\b(\d{1,3})\s*(?:years?\s*old|y/?o)\b"), lambda m: to_age_band(int(m.group(1)))),  # 34 years old
    (re.compile(r"\bteen(?:s|ager)?\b|십대|청소년"), lambda m: "teens"),
)

_AGE_GENERAL = (
    (re.compile(r"\badult\b|성인"), "adult"),
    (re.compile(r"\bsenior\b|\belderly\b|노년|고령|노인"), "senior"),
)


def age_candidates(raw_value: str, value_en: str | None) -> set[str]:
    """
    input : raw_value ("30대 중반"), value_en ("mid-30s")
    output : 후보 값 집합 ({"30s"})

    10년 단위 값이 하나도 없을 때만 adult/senior 같은 일반화 표현을 본다.
    value_en이 숫자 하나뿐이면 정확한 나이로 본다 ("34" → "30s").
    """
    text = _join(raw_value, value_en)
    bands: set[str] = set()

    for word, decade in _ENGLISH_DECADE_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            bands.add(f"{decade}s")
    for pattern, make in _AGE_PATTERNS:
        for match in pattern.finditer(text):
            bands.add(make(match))
    if value_en and value_en.strip().isdigit():
        bands.add(to_age_band(int(value_en.strip())))

    if bands:
        return bands
    return {value for pattern, value in _AGE_GENERAL if pattern.search(text)}


# ──────────────────────────────────────────────────────────────
# EDU
# 주의: risk_defender의 derive_education은 "studying towards a Bachelors"를 College Degree로 분류한다
#       (bachelor를 studying보다 먼저 검사). 그래서 기준 집단에 "In College/HS Diploma"가 0명이다.
#       여기서는 뜻대로 재학생을 In College로 해석하고, 기준 집단에 없으면 normalizer가 UNMAPPED로 막는다.
# ──────────────────────────────────────────────────────────────

_EDU_RULES = (
    ("PhD", re.compile(r"박사|ph\.?\s?d|doctor(?:ate|al)")),
    ("Master's", re.compile(r"석사|master")),
    ("College Degree", re.compile(r"학사|대졸|대학교?\s*졸업|bachelor|college degree|university degree|undergraduate degree")),
    ("In College/HS Diploma", re.compile(r"대학생|재학|studying|in college|college student|undergraduate student")),
    ("HS Diploma", re.compile(r"고졸|고등학교|high school")),
)

_EDU_GENERAL = (
    ("higher_education", re.compile(r"고등\s*교육|대학\s*이상|higher education|tertiary")),
)


def edu_candidates(raw_value: str, value_en: str | None) -> set[str]:
    """
    input : raw_value ("석사"), value_en ("Master's degree")
    output : 후보 값 집합 ({"Master's"})

    "석사 과정 재학 중"처럼 둘 이상 걸리면 후보가 2개가 되어 PENDING이 된다 (학위 취득 여부가 모호).
    """
    text = _join(raw_value, value_en)
    found = {value for value, pattern in _EDU_RULES if pattern.search(text)}
    if found:
        return found
    return {value for value, pattern in _EDU_GENERAL if pattern.search(text)}


# ──────────────────────────────────────────────────────────────
# REL
# "unmarried"/"결혼하지 않은"이 "married"/"결혼"에 걸리지 않도록 영어는 단어 경계, 한국어는 부정 lookahead를 쓴다.
# ──────────────────────────────────────────────────────────────

_REL_RULES = (
    ("single", re.compile(r"미혼|싱글|독신|\bsingle\b|\bunmarried\b|not married|결혼하지 않")),
    ("married", re.compile(r"기혼|결혼(?!하지)|배우자|남편|아내|\bmarried\b|\bspouse\b|\bhusband\b|\bwife\b")),
    ("engaged", re.compile(r"약혼|\bengaged\b|fianc")),
    ("in a relationship", re.compile(r"연애|교제|사귀|여자\s*친구|남자\s*친구|in a relationship|\bdating\b|girlfriend|boyfriend")),
    ("divorced", re.compile(r"이혼|\bdivorced\b")),
    ("widowed", re.compile(r"사별|\bwidow")),
)


def rel_candidates(raw_value: str, value_en: str | None) -> set[str]:
    """
    input : raw_value ("미혼"), value_en ("single")
    output : 후보 값 집합 ({"single"})
    """
    text = _join(raw_value, value_en)
    return {value for value, pattern in _REL_RULES if pattern.search(text)}


def clean_free_text(text: str) -> str:
    """
    OCCP처럼 자유 문자열을 대조할 때 쓰는 최소 정리.
    소문자·공백 정리, 앞의 관사(a/an/the)와 끝 마침표 제거만 한다. 단어를 바꾸지 않는다.
    """
    cleaned = " ".join(text.strip().lower().split()).rstrip(".")
    return re.sub(r"^(?:a|an|the)\s+", "", cleaned)
