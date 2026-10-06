# 고정 표와 기준 집단의 도시 목록으로 Ontology를 만든다
#
# 도시 node는 기준 집단에 실제로 나온 도시만 만든다 (거주지 LOC, 출생지 POB 각각).
#   D1은 도시 표기 자원으로 GeoNames를 들지만, 실행 환경에 그 자료가 없어 기준 집단 목록으로 한정했다.
#   기준 집단에 없는 도시가 응답에 나오면 국가 node로만 연결된다 (normalizer.py).
#
# node_id 규칙
#   AGE  : AGE:Y34 (정확한 나이) → AGE:5Y30 (30–34) → AGE:10Y30 (30–39) → AGE:ANY
#   LOC  : LOC:CH:zurich → LOC:C:CH → LOC:ANY          POB도 같은 모양
#   OCCP : OCCP:2512 → OCCP:251 → OCCP:25 → OCCP:2 → OCCP:ANY,  분류 불가는 OCCP:X → OCCP:ANY
#   EDU·INC·REL·SEX : tables.py의 접미사 (예: EDU:MASTER → EDU:TERTIARY → EDU:ANY)

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping

from core.schemas import QI_TYPES
from ontology.isco import (
    ISCO_LABELS,
    LEVEL_BY_LENGTH,
    UNCLASSIFIED_CODE,
    UNCLASSIFIED_LABELS,
    OccupationTable,
    load_occupation_table,
)
from ontology.model import Node, Ontology, root_id
from ontology.tables import (
    AGE_MAX,
    AGE_MIN,
    CATEGORICAL_NODES,
    CITY_ALIASES,
    COUNTRIES,
    SUBDIVISION_TO_COUNTRY,
)

ONTOLOGY_VERSION = "d1-core-0.1"

# 기준 집단 profile에서 QI 원본 값을 읽는 필드 (SynthPAI profile 형식)
PROFILE_FIELD_BY_QI: dict[str, str] = {
    "AGE": "age",
    "SEX": "sex",
    "LOC": "city_country",
    "OCCP": "occupation",
    "EDU": "education",
    "REL": "relationship_status",
    "INC": "income_level",
    "POB": "birth_city_country",
}
GEO_ATTRIBUTES = ("LOC", "POB")


# ──────────────────────────────────────────────────────────────
# ID 만들기
# ──────────────────────────────────────────────────────────────

def age_exact_id(age: int) -> str:
    return f"AGE:Y{age}"


def age_5y_id(age: int) -> str:
    return f"AGE:5Y{age // 5 * 5}"


def age_10y_id(age: int) -> str:
    return f"AGE:10Y{age // 10 * 10}"


def country_id(attribute: str, iso: str) -> str:
    return f"{attribute}:C:{iso}"


def city_id(attribute: str, iso: str, city: str) -> str:
    return f"{attribute}:{iso}:{slug(city)}"


def occupation_id(code: str) -> str:
    return f"OCCP:{code}"


def slug(text: str) -> str:
    """도시 이름 → ID용 문자열. 소문자, 악센트 제거, 글자·숫자 외에는 '_'."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii") or text
    return re.sub(r"[^0-9a-z가-힣]+", "_", plain.strip().lower()).strip("_")


def country_alias_map() -> dict[str, str]:
    """국가 표기(소문자) → ISO 코드. 영어·한국어 이름과 다른 표기."""
    aliases: dict[str, str] = {}
    for iso, english, korean, others in COUNTRIES:
        for name in (english, korean, *others):
            aliases[name.strip().lower()] = iso
    return aliases


def place_key(text: str) -> str:
    """국가·주 이름 대조용: 소문자, 공백 정리, 앞의 'the ' 제거."""
    cleaned = " ".join(str(text).strip().lower().split()).rstrip(".")
    return cleaned[4:] if cleaned.startswith("the ") else cleaned


def region_candidates(text: str, countries: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """
    input : text (쉼표 뒤에 온 지역 이름. 국가이거나 주·구성국)
    output : 가능한 ISO 코드들 (국가로 읽은 것 먼저). "Georgia"는 ("GE", "US") 둘 다 나온다
    """
    key = place_key(text)
    countries = countries if countries is not None else country_alias_map()
    found = [countries[key]] if key in countries else []
    if key in SUBDIVISION_TO_COUNTRY and SUBDIVISION_TO_COUNTRY[key] not in found:
        found.append(SUBDIVISION_TO_COUNTRY[key])
    return tuple(found)


def canonical_city(name: str) -> str:
    """도시 이름의 표준 표기 (CITY_ALIASES 적용, 공백 정리)."""
    cleaned = " ".join(str(name).split())
    return CITY_ALIASES.get(slug(cleaned), cleaned)


def split_city_country(value: str) -> tuple[str | None, str | None]:
    """'Zurich, Switzerland' → ('Zurich', 'Switzerland'). 쉼표가 없으면 (None, 값)이 아니라 (값, None)."""
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[-1]


# ──────────────────────────────────────────────────────────────
# 조립
# ──────────────────────────────────────────────────────────────

def build_ontology(
    profiles: Iterable[Mapping[str, Any]],
    *,
    occupation_table: OccupationTable | None = None,
) -> Ontology:
    """
    input : profiles (기준 집단의 원본 profile들. 도시 목록을 뽑는 데만 쓴다),
            occupation_table (없으면 기본 대응표를 읽는다)
    output : Ontology

    같은 입력이면 같은 node 집합과 같은 해시가 나온다.
    """
    occupation_table = occupation_table or load_occupation_table()
    nodes: list[Node] = [Node(root_id(qi), qi, None, "any", "전체", "any") for qi in QI_TYPES]

    nodes.extend(_age_nodes())
    cities = _collect_cities(profiles)
    nodes.extend(_geo_nodes(cities))
    nodes.extend(_occupation_nodes(occupation_table))
    nodes.extend(_categorical_nodes())

    sources = {
        "occupation_alias": occupation_table.sha256,
        "countries": _digest(COUNTRIES),
        "categorical": _digest(CATEGORICAL_NODES),
        "cities": _digest({attr: sorted(values) for attr, values in cities.items()}),
        "age_range": f"{AGE_MIN}-{AGE_MAX}",
    }
    return Ontology(nodes, version=ONTOLOGY_VERSION, sources=sources)


def _age_nodes() -> list[Node]:
    nodes: list[Node] = []
    root = root_id("AGE")
    for decade in range(AGE_MIN // 10 * 10, AGE_MAX + 1, 10):
        ko, en = (f"{decade}대", f"{decade}s") if decade != 10 else ("10대", "teens")
        nodes.append(Node(age_10y_id(decade), "AGE", root, "10y", ko, en))
        for start, ko_part, en_part in ((decade, "초반", "early"), (decade + 5, "후반", "late")):
            en_label = f"{en_part} {decade}s" if decade != 10 else f"{en_part} teens"
            nodes.append(Node(age_5y_id(start), "AGE", age_10y_id(decade), "5y",
                              f"{decade}대 {ko_part}", en_label))
    for age in range(AGE_MIN, AGE_MAX + 1):
        nodes.append(Node(age_exact_id(age), "AGE", age_5y_id(age), "exact", f"{age}세", f"{age} years old"))
    return nodes


def _collect_cities(profiles: Iterable[Mapping[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    """QI별 (ISO, 도시 이름) 집합. 국가를 알 수 없는 값은 도시 node를 만들지 않는다."""
    aliases = country_alias_map()
    found: dict[str, set[tuple[str, str]]] = {attr: set() for attr in GEO_ATTRIBUTES}
    for profile in profiles:
        for attribute in GEO_ATTRIBUTES:
            value = profile.get(PROFILE_FIELD_BY_QI[attribute])
            if not value:
                continue
            city, country = split_city_country(value)
            candidates = region_candidates(country or "", aliases)
            if city and candidates:
                # 기준 집단 값은 "도시, 국가" 형식이다. 국가로 읽을 수 있으면 국가로 읽는다 (Tbilisi, Georgia → GE)
                found[attribute].add((candidates[0], canonical_city(city)))
    return found


def _geo_nodes(cities: Mapping[str, set[tuple[str, str]]]) -> list[Node]:
    english = {iso: name for iso, name, _, _ in COUNTRIES}
    nodes: list[Node] = []
    for attribute in GEO_ATTRIBUTES:
        for iso, name, korean, _ in COUNTRIES:
            nodes.append(Node(country_id(attribute, iso), attribute, root_id(attribute), "country", korean, name))
        seen: set[str] = set()
        for iso, city in sorted(cities[attribute]):
            node_id = city_id(attribute, iso, city)
            if node_id in seen:        # 대소문자·악센트만 다른 표기는 한 node
                continue
            seen.add(node_id)
            label = f"{city}, {english[iso]}"
            nodes.append(Node(node_id, attribute, country_id(attribute, iso), "city", label, label))
    return nodes


def _occupation_nodes(table: OccupationTable) -> list[Node]:
    root = root_id("OCCP")
    nodes = [Node(occupation_id(UNCLASSIFIED_CODE), "OCCP", root, "unclassified", *UNCLASSIFIED_LABELS)]
    for code in sorted(table.codes, key=lambda c: (len(c), c)):
        korean, english = ISCO_LABELS[code]
        parent = occupation_id(code[:-1]) if len(code) > 1 else root
        nodes.append(Node(occupation_id(code), "OCCP", parent, LEVEL_BY_LENGTH[len(code)],
                          korean or english, english))
    return nodes


def _categorical_nodes() -> list[Node]:
    nodes: list[Node] = []
    for attribute, rows in CATEGORICAL_NODES.items():
        for suffix, parent_suffix, level, korean, english in rows:
            parent = f"{attribute}:{parent_suffix}" if parent_suffix else root_id(attribute)
            nodes.append(Node(f"{attribute}:{suffix}", attribute, parent, level, korean, english))
    return nodes


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=list).encode("utf-8")).hexdigest()
