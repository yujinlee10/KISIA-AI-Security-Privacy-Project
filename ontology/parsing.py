# 표현(한국어·영어) → node 해석 규칙
#
# 기준 집단 profile 값("Masters in Computer Science")과 실행 중 추출된 값("석사", "Master's degree")을
# 같은 규칙으로 해석한다. 두 쪽이 다른 규칙을 쓰면 같은 사람이 서로 다른 node로 갈려 k가 틀어진다.
#
# 결과는 후보 node 목록이다.
#   0개  → 해석 못 함 (UNMAPPED)
#   1개  → 확정 (MAPPED)
#   2개↑ → 모호 (PENDING). 추측해서 하나를 고르지 않는다 (D1).
# 여러 표현이 한 줄기(조상–자손)를 가리키면 가장 구체적인 것 하나로 본다 ("34세, 30대" → 34세).
#
# 이 파일은 LLM을 부르지 않는다. 같은 입력에 항상 같은 결과가 나온다.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ontology.builder import (
    GEO_ATTRIBUTES,
    age_10y_id,
    age_5y_id,
    age_exact_id,
    canonical_city,
    country_alias_map,
    country_id,
    occupation_id,
    place_key,
    region_candidates,
    slug,
)
from ontology.isco import OccupationTable, clean_occupation
from ontology.model import Ontology
from ontology.tables import AGE_MAX, AGE_MIN


@dataclass(frozen=True)
class Parsed:
    """
    node_ids → 후보 node (위 설명)
    reason   → 어떤 규칙으로 정해졌는지. normalizer.MappingReason의 이름과 같은 문자열을 쓴다
    """
    node_ids: tuple[str, ...]
    reason: str

    @property
    def node_id(self) -> str | None:
        return self.node_ids[0] if len(self.node_ids) == 1 else None


NOT_PARSED = Parsed((), "NO_RULE")


class Parser:
    def __init__(self, ontology: Ontology, occupation_table: OccupationTable) -> None:
        self._ontology = ontology
        self._occupations = occupation_table
        self._countries = country_alias_map()

        # 도시 이름 → [(ISO, node_id)] (QI별). node의 영어 이름 "City, Country"에서 도시 부분을 꺼낸다.
        self._cities: dict[str, dict[str, list[tuple[str, str]]]] = {attr: {} for attr in GEO_ATTRIBUTES}
        for attribute in GEO_ATTRIBUTES:
            for node in ontology.nodes(attribute):
                if node.level != "city":
                    continue
                iso = node.node_id.split(":")[1]
                name = node.label_en.split(",")[0]
                self._cities[attribute].setdefault(slug(name), []).append((iso, node.node_id))

        # ISCO 그룹 이름 → node. 단계가 달라도 이름이 같은 그룹이 있어(예: 231과 2310) 상위 그룹을 택한다.
        self._occupation_labels: dict[str, str] = {}
        for node in sorted(ontology.nodes("OCCP"), key=lambda n: len(n.node_id), reverse=True):
            if node.is_root:
                continue
            for label in (node.label_en, node.label_ko):
                self._occupation_labels[_key(label)] = node.node_id

    def parse(self, attribute: str, *texts: str | None) -> Parsed:
        """
        input : attribute (QI 코드), texts (같은 값을 가리키는 표현들. 보통 raw_value와 value_en)
        output : Parsed
        """
        present = [str(t) for t in texts if t is not None and str(t).strip()]
        if not present:
            return NOT_PARSED
        # "스위스 (Switzerland)"처럼 한 표현 안에 두 표기가 묶여 오면 풀어서 각각도 본다
        for text in list(present):
            both = _PAIRED.match(text.strip())
            if both:
                present.extend(part for part in both.groups() if part.strip())
        if attribute == "AGE":
            return self._age(present)
        if attribute in GEO_ATTRIBUTES:
            return self._geo(attribute, present)
        if attribute == "OCCP":
            return self._occupation(present)
        joined = " ".join(present).lower()
        rule = {"EDU": _edu, "INC": _inc, "REL": _rel, "SEX": _sex}[attribute]
        return self._finish(attribute, rule(joined), "RULE_MATCH")

    # ── AGE ──────────────────────────────────────────────

    def _age(self, texts: list[str]) -> Parsed:
        found: set[str] = set()
        per_text: list[set[str]] = []
        for text in texts:
            nodes_of_text = {node for node in _age_nodes(text.lower()) if node in self._ontology}
            per_text.append(nodes_of_text)
            found.update(nodes_of_text)
        parsed = self._finish("AGE", found, "RULE_MATCH")
        if parsed.reason != "AMBIGUOUS":
            return self._cap_by_explicit_range(parsed, texts)
        # 표현들이 서로 다른 구간을 가리킬 때 ("30대 초중반(28-35세)" + "early 30s"):
        # 뒤에 온 표현(추출기의 표준 표기)이 한 구간으로 읽히면 그 구간이 속한 10년 구간까지만 인정한다.
        # 5년 구간으로 좁히지 않는 것은 원문이 그보다 넓은 범위를 말했기 때문이다.
        for nodes_of_text in reversed(per_text):
            single = self._finish("AGE", nodes_of_text, "RULE_MATCH")
            if single.node_id is not None:
                node_id = single.node_id
                while self._ontology.node(node_id).level != "10y":
                    parent = self._ontology.parent(node_id)
                    if parent is None or self._ontology.node(parent).is_root:
                        break
                    node_id = parent
                if self._ontology.node(node_id).level == "10y":
                    return Parsed((node_id,), "RULE_MATCH")
        return parsed

    def _cap_by_explicit_range(self, parsed: Parsed, texts: list[str]) -> Parsed:
        """
        표현에 숫자 범위가 함께 적혀 있으면 그 범위보다 좁게 읽지 않는다.
        "30대 초중반(대략 30-35세)"은 '초반'만 보면 30~34세 구간이지만 적힌 범위는 35세까지다 → 30대까지만 인정한다.
        (10/07 실행: 35세인 대상이 30~34세 구간으로 들어가 틀린 값이 됐다)
        """
        if parsed.node_id is None:
            return parsed
        ranges: set[str] = set()
        for text in texts:
            for match in _RANGE.finditer(text.lower()):
                low, high = sorted((int(match.group(1)), int(match.group(2))))
                if not (_in_range(low) and _in_range(high)) or low == high:
                    continue
                if low // 5 == high // 5:
                    ranges.add(age_5y_id(low))
                elif low // 10 == high // 10:
                    ranges.add(age_10y_id(low))
        ranges = {node for node in ranges if node in self._ontology}
        if len(ranges) == 1:
            (limit,) = ranges
            if parsed.node_id != limit and self._ontology.is_same_or_descendant(parsed.node_id, limit):
                return Parsed((limit,), parsed.reason)
        return parsed

    # ── LOC · POB ────────────────────────────────────────

    def _geo(self, attribute: str, texts: list[str]) -> Parsed:
        best: Parsed = NOT_PARSED
        for text in texts:
            parsed = self._geo_one(attribute, text)
            if parsed.node_id is not None:
                # 한 표현이 도시까지, 다른 표현이 국가까지만 풀렸다면 더 구체적인 쪽을 쓴다
                if best.node_id is None or self._ontology.depth(parsed.node_id) > self._ontology.depth(best.node_id):
                    best = parsed
            elif not best.node_ids:
                best = parsed
        return best

    def _geo_one(self, attribute: str, text: str) -> Parsed:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return NOT_PARSED

        if len(parts) >= 2:
            # "도시, 지역": 지역은 국가이거나 주·구성국이다. "Georgia"처럼 둘 다인 이름은 후보가 둘이다.
            candidates = region_candidates(parts[-1], self._countries)
            if not candidates:
                return NOT_PARSED          # 모르는 지역 이름이면 도시 이름만으로 추측하지 않는다 (Paris, Ontario)
            entries = self._cities[attribute].get(slug(canonical_city(parts[0])), [])
            matches = [node for iso, node in entries if iso in candidates]
            if len(matches) == 1:
                return Parsed((matches[0],), "EXACT_MATCH")
            if len(candidates) == 1 and not matches:
                # 앞부분이 다른 나라의 지역이면 ("중서부, 스위스") 두 곳을 나열한 것이다. 한 나라로 정하지 않는다
                other = [iso for iso in self._area_isos(parts[0]) if iso not in candidates]
                if other:
                    return Parsed(tuple(sorted(country_id(attribute, iso) for iso in (*candidates, *other))),
                                  "AMBIGUOUS")
                return Parsed((country_id(attribute, candidates[0]),), "COUNTRY_MATCH")   # 목록에 없는 도시 → 국가까지만
            nodes = matches or [country_id(attribute, iso) for iso in candidates]
            return Parsed(tuple(sorted(nodes)), "AMBIGUOUS")

        iso = self._countries.get(place_key(parts[0]))
        if iso is not None:
            return Parsed((country_id(attribute, iso),), "COUNTRY_MATCH")
        entries = self._cities[attribute].get(slug(canonical_city(parts[0])), [])
        if len(entries) == 1:
            return Parsed((entries[0][1],), "CITY_MATCH")
        if len(entries) > 1:
            return Parsed(tuple(sorted(node for _, node in entries)), "AMBIGUOUS")
        subdivision = [c for c in region_candidates(parts[0], {}) ]
        if len(subdivision) == 1:
            return Parsed((country_id(attribute, subdivision[0]),), "COUNTRY_MATCH")     # "Texas" → 미국
        # "미국 중서부", "the Midwest"처럼 나라 이름이나 널리 쓰이는 권역 이름이 섞인 표현
        if not _GEO_UNCERTAIN.search(text.lower()):
            isos = self._area_isos(parts[0])
            if len(isos) == 1:
                return Parsed((country_id(attribute, isos[0]),), "COUNTRY_MATCH")
            if len(isos) > 1:
                return Parsed(tuple(sorted(country_id(attribute, iso) for iso in isos)), "AMBIGUOUS")
        return Parsed((), "NOT_IN_REFERENCE")

    def _area_isos(self, text: str) -> tuple[str, ...]:
        """표현 안에 들어 있는 나라·주·권역 이름이 가리키는 국가들 (온톨로지에 있는 국가만, 중복 없이)."""
        lowered = place_key(text)
        found: list[str] = []
        for iso in region_candidates(lowered, self._countries):
            found.append(iso)
        for word in re.split(r"[\s/·]+", lowered):
            for iso in region_candidates(word.strip("()'\"의"), self._countries):
                found.append(iso)
        for name, iso in _AREA_HINTS.items():
            if name in lowered:
                found.append(iso)
        unique = [iso for position, iso in enumerate(found) if iso not in found[:position]]
        return tuple(iso for iso in unique if country_id("LOC", iso) in self._ontology)

    # ── OCCP ─────────────────────────────────────────────

    def _occupation(self, texts: list[str]) -> Parsed:
        for text in texts:
            code = _ISCO_CODE.search(text)
            if code and occupation_id(code.group(1)) in self._ontology:
                return Parsed((occupation_id(code.group(1)),), "EXACT_MATCH")
        for text in texts:
            alias = self._occupations.lookup(text)
            if alias is not None:
                return Parsed((occupation_id(alias.isco_code),), "EXACT_MATCH")
        for text in texts:
            node_id = self._occupation_labels.get(_key(text))
            if node_id is not None:
                return Parsed((node_id,), "GENERALIZED_NODE")
        return Parsed((), "NOT_IN_REFERENCE")

    # ── 후보 정리 ────────────────────────────────────────

    def _finish(self, attribute: str, nodes: Iterable[str], reason: str) -> Parsed:
        unique = sorted(set(nodes))
        if not unique:
            return NOT_PARSED
        if len(unique) == 1:
            return Parsed((unique[0],), reason)
        single = self._ontology.most_specific(unique)
        if single is not None:
            return Parsed((single,), reason)
        return Parsed(tuple(unique), "AMBIGUOUS")


def _key(text: str) -> str:
    return clean_occupation(text)


# ──────────────────────────────────────────────────────────────
# AGE 규칙
#   정확한 나이  : "34세", "34살", "서른네 살", "34 years old", "34-year-old", "aged 34", "age: 34", 숫자만 있는 값("34")
#   10년 구간    : "30대", "삼십대", "30s", "thirties"   ("중반"/"mid"가 붙어도 10년 구간: 33~37은 5년 구간 둘에 걸친다)
#   5년 구간     : "30대 초반·후반", "서른 초반", "early/late 30s", "25-29", "25~29세"
#   범위         : 두 나이를 모두 담는 가장 좁은 구간. 10년 구간을 넘으면 양쪽 구간이 후보가 되어 모호 처리된다
#   읽지 않는 것 : "60세 이상", "30세 미만", "over 60"처럼 한쪽이 열린 범위 (맞는 node가 없다), "20세기"
# ──────────────────────────────────────────────────────────────

# 나라보다 넓거나 좁은 권역 이름 가운데 한 나라로 읽을 수 있는 것 (응답에 자주 나온 표현만. 필요하면 늘린다)
_AREA_HINTS = {
    "midwest": "US", "중서부": "US", "great lakes": "US", "오대호": "US", "그레이트 레이크": "US",
    "new england": "US", "뉴잉글랜드": "US", "east coast": "US", "west coast": "US", "silicon valley": "US", "실리콘밸리": "US",
}
# 여러 곳을 놓고 고르는 표현이나 부정 표현이면 나라를 정하지 않는다
_GEO_UNCERTAIN = re.compile(r"또는|혹은|이나\s|\bor\b|아닌|아니|외의|밖|outside|not\s|either|유럽|europe|아시아|asia")

_PAIRED = re.compile(r"^(.+?)\s*\((.+)\)$")
_ISCO_CODE = re.compile(r"isco(?:[-\s]?08)?[\s:#]*(\d{1,4})\b", re.IGNORECASE)

_DECADE_WORDS = {"twenties": 20, "thirties": 30, "forties": 40, "fifties": 50,
                 "sixties": 60, "seventies": 70, "eighties": 80, "nineties": 90}
_KO_SINO_DECADES = {"이십": 20, "삼십": 30, "사십": 40, "오십": 50, "육십": 60, "칠십": 70, "팔십": 80, "구십": 90}
_KO_NATIVE_TENS = {"스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_KO_NATIVE_ONES = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
                   "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_QUALIFIER_EN = {"early": "early", "mid": "mid", "late": "late"}
_QUALIFIER_KO = {"초반": "early", "중반": "mid", "후반": "late"}

_OPEN_RANGE = re.compile(
    r"\d{1,3}\s*(?:세|살|대)?\s*(?:이상|이하|미만|초과|넘|안\s*됨)"
    r"|\b(?:over|under|above|below|at least|at most|more than|less than|younger than|older than)\s*\d{1,3}"
    r"|\d{1,3}\s*(?:\+|or (?:older|younger|over|under)|and (?:over|under|above))"
    r"|\d{1,2}\s*세기")
_KO_DECADE_RANGE = re.compile(r"(\d)0\s*[-~–—]\s*(\d)0\s*대")
_RANGE = re.compile(r"(?<!\d)(\d{2})\s*(?:[-~–—]|to)\s*(\d{2})(?!\d)")
_KO_DECADE = re.compile(r"(\d)0\s*대(?:\s*(초반|중반|후반))?")
_KO_SINO_DECADE = re.compile("(" + "|".join(_KO_SINO_DECADES) + r")\s*대(?:\s*(초반|중반|후반))?")
_KO_NATIVE_QUALIFIED = re.compile("(" + "|".join(_KO_NATIVE_TENS) + r")\s*(초반|중반|후반)")
_KO_NATIVE_EXACT = re.compile(
    "(" + "|".join(_KO_NATIVE_TENS) + r")\s*(" + "|".join(sorted(_KO_NATIVE_ONES, key=len, reverse=True)) + r")?\s*(?:살|세)")
_EN_DECADE = re.compile(r"\b(?:(early|mid|late)[-\s]*)?(\d)0'?s\b")
_EN_DECADE_WORD = re.compile(r"\b(?:(early|mid|late)[-\s]*)?(" + "|".join(_DECADE_WORDS) + r")\b")
_TEENS = re.compile(r"\b(?:(early|mid|late)[-\s]*)?teen(?:s|ager|agers|age)?\b|청소년")
_EXACT_PATTERNS = (
    re.compile(r"(?<!\d)(\d{1,3})\s*(?:세|살)"),
    re.compile(r"(?<!\d)(\d{1,3})[-\s]*(?:years?[-\s]*old|yrs?\.?\s*old|y/?o)\b"),
    re.compile(r"\bage[d]?\s*:?\s*(\d{1,3})(?!\d)"),
    re.compile(r"\b(?:i'm|i am)\s+(\d{2})(?![\d%])"),
)


def _in_range(age: int) -> bool:
    return AGE_MIN <= age <= AGE_MAX


def _decade_node(decade: int, qualifier: str | None) -> str | None:
    if not _in_range(decade):
        return None
    if qualifier == "early":
        return age_5y_id(decade)
    if qualifier == "late":
        return age_5y_id(decade + 5)
    return age_10y_id(decade)


def _age_nodes(text: str) -> set[str]:
    nodes: set[str] = set()

    def take(pattern: re.Pattern[str], handler) -> None:
        nonlocal text
        for match in pattern.finditer(text):
            handler(match)
        text = pattern.sub(" ", text)     # 한 번 해석한 구간은 뒤 규칙이 다시 읽지 않게 지운다

    def decade_range(match: re.Match[str]) -> None:
        for digit in (match.group(1), match.group(2)):
            add(_decade_node(int(digit) * 10, None))

    def age_range(match: re.Match[str]) -> None:
        low, high = sorted((int(match.group(1)), int(match.group(2))))
        if not (_in_range(low) and _in_range(high)):
            return
        if low == high:
            nodes.add(age_exact_id(low))
        elif low // 5 == high // 5:
            nodes.add(age_5y_id(low))
        elif low // 10 == high // 10:
            nodes.add(age_10y_id(low))
        else:
            nodes.update({age_10y_id(low), age_10y_id(high)})

    def add(node: str | None) -> None:
        if node:
            nodes.add(node)

    def exact(age: int) -> None:
        if _in_range(age):
            nodes.add(age_exact_id(age))

    take(_OPEN_RANGE, lambda m: None)     # 한쪽이 열린 범위는 읽지 않고 지운다
    take(_KO_DECADE_RANGE, decade_range)
    take(_RANGE, age_range)
    take(_KO_DECADE, lambda m: add(_decade_node(int(m.group(1)) * 10, _QUALIFIER_KO.get(m.group(2) or ""))))
    take(_KO_SINO_DECADE, lambda m: add(_decade_node(_KO_SINO_DECADES[m.group(1)], _QUALIFIER_KO.get(m.group(2) or ""))))
    take(_KO_NATIVE_QUALIFIED, lambda m: add(_decade_node(_KO_NATIVE_TENS[m.group(1)], _QUALIFIER_KO[m.group(2)])))
    take(_KO_NATIVE_EXACT, lambda m: exact(_KO_NATIVE_TENS[m.group(1)] + _KO_NATIVE_ONES.get(m.group(2) or "", 0)))
    take(_EN_DECADE, lambda m: add(_decade_node(int(m.group(2)) * 10, _QUALIFIER_EN.get(m.group(1) or ""))))
    take(_EN_DECADE_WORD, lambda m: add(_decade_node(_DECADE_WORDS[m.group(2)], _QUALIFIER_EN.get(m.group(1) or ""))))
    take(_TEENS, lambda m: add(_decade_node(10, _QUALIFIER_EN.get(m.group(1) or ""))))
    for pattern in _EXACT_PATTERNS:
        take(pattern, lambda m: exact(int(m.group(1))))
    if text.strip().isdigit():
        exact(int(text.strip()))
    return nodes


# ──────────────────────────────────────────────────────────────
# EDU 규칙 — 값은 "지금까지 마친 최고 학력"으로 읽는다
#   학위 표현           : 박사 / 석사·MBA / 학사
#   재학·과정 중        : 학사 과정 → IN_COLLEGE, 석사 과정 → COLLEGE(학사는 마침), 박사 과정 → TERTIARY(석사 여부 불명)
#   학위 수준 불명      : "Law Degree", "MD"·"Doctorate in Medicine", "Diploma in …", 전문학사 → TERTIARY
#                        (가장 구체적으로 뒷받침되는 node에서 멈춘다)
#   여러 학위가 함께 나옴 : 가장 높은 것
#   읽지 않는 것        : "학위 없음", "some college", 대학·고교 중퇴, 고등학교 재학 (맞는 node가 없다)
# ──────────────────────────────────────────────────────────────

# "~ 이상" 표현: 그 학력이거나 더 높다는 뜻이라 한 단계로 정할 수 없다
_EDU_AT_LEAST_HS = re.compile(
    r"(?:고등학교|고졸|high\s*school)[^.;]{0,14}?(?:이상|or (?:higher|above|more))|at least (?:a )?high\s*school")
_EDU_AT_LEAST_COLLEGE = re.compile(
    r"(?:대학교?|대졸|학사|석사|college|university|bachelor(?:'s)?(?: degree)?|master(?:'s)?(?: degree)?)[^.;]{0,10}?"
    r"(?:이상|or (?:higher|above|more))|at least (?:a )?(?:bachelor|college|university|master)")
_EDU_NO_NODE = re.compile(
    r"no (?:college )?degree|without a degree|학위\s*(?:가|는)?\s*없|some college|college dropout|dropped out"
    r"|high\s*school dropout|did(?:n't| not) (?:finish|complete|graduate)|중퇴|no high\s*school")
_EDU_MEDICAL = re.compile(r"\bm\.?d\.?(?=\s|$)|doctor(?:ate)? (?:of|in) medicine")
_EDU_ASSOCIATE = re.compile(r"전문\s*학사|associate(?:'s)? degree")
_EDU_DOCTORATE = re.compile(r"ph\.?\s?d|doctorate|doctoral|박사")
_EDU_MASTER = re.compile(r"master|\bmba\b|\bm\.?sc\b|\bm\.a\.|\bm\.s\.|석사")
_EDU_BACHELOR = re.compile(
    r"bachelor|\bb\.?sc\b|\bb\.a\.|\bb\.s\.|학사|college degree|university degree|undergraduate degree"
    r"|college graduate|university graduate|graduated from (?:a |the )?(?:college|university)"
    r"|대졸|대학교?(?:를|을)?\s*졸업")
_EDU_HIGH_SCHOOL = re.compile(r"high\s*school|\bhs\b|고졸|고등학교")
_EDU_ENROLLED = re.compile(
    r"studying|in college|college student|\bstudent\b|candidate|pursuing|working towards|enrolled"
    r"|재학|대학생|수료|과정\s*중|(?:박사|석사|학사)\s*과정(?!\s*(?:을|를)?\s*(?:졸업|마치|마쳤|이수))")
_EDU_GRADUATE_STUDENT = re.compile(r"(?<!under)graduate student|grad student|대학원생|대학원\s*재학")
_EDU_TERTIARY = re.compile(r"tertiary|higher education|대학\s*이상|고등\s*교육|대학원|diploma|degree|학위")


def _edu(text: str) -> list[str]:
    if _EDU_NO_NODE.search(text) or _EDU_AT_LEAST_HS.search(text):
        return []                          # "고등학교 졸업 이상": 고졸인지 그보다 높은지 정할 수 없다
    if _EDU_AT_LEAST_COLLEGE.search(text):
        return ["EDU:TERTIARY"]            # "대학 이상": 학사인지 석·박사인지 정할 수 없어 묶음 단계까지만
    # 학위 수준을 정할 수 없는 표현은 먼저 떼어 둔다 ("Doctorate in Medicine"의 doctorate를 박사로 읽지 않기 위해)
    unspecified = bool(_EDU_MEDICAL.search(text) or _EDU_ASSOCIATE.search(text))
    text = _EDU_ASSOCIATE.sub(" ", _EDU_MEDICAL.sub(" ", text))

    doctorate, master, bachelor = (bool(p.search(text)) for p in (_EDU_DOCTORATE, _EDU_MASTER, _EDU_BACHELOR))
    high_school = bool(_EDU_HIGH_SCHOOL.search(text))

    if _EDU_GRADUATE_STUDENT.search(text) and not (doctorate or master):
        return ["EDU:COLLEGE"]
    if _EDU_ENROLLED.search(text):
        if doctorate:
            return ["EDU:TERTIARY"]
        if master:
            return ["EDU:COLLEGE"]
        if high_school and not bachelor:
            return []                      # 고등학교 재학: 해당 node 없음
        return ["EDU:IN_COLLEGE"]
    if doctorate:
        return ["EDU:DOCTORATE"]
    if master:
        return ["EDU:MASTER"]
    if bachelor:
        return ["EDU:COLLEGE"]
    if unspecified:
        return ["EDU:TERTIARY"]
    if high_school:
        return ["EDU:HS"]
    if _EDU_TERTIARY.search(text):
        return ["EDU:TERTIARY"]
    return []


# ──────────────────────────────────────────────────────────────
# INC · REL · SEX 규칙
# ──────────────────────────────────────────────────────────────

# 부정·비교 표현은 어느 등급인지 정할 수 없으므로 읽지 않는다 ("not high", "높지 않다", "평균 이하").
_INC_NO_NODE = re.compile(
    r"not (?:very |so |that |particularly )?(?:high|low)|(?:높|낮)지\s*않|(?:below|above)[-\s]average"
    r"|평균\s*(?:이하|이상|보다)")
_INC_SPAN = re.compile(r"평균\s*이하\s*[~\-–—]|하단\s*[~\-–—]|(?:low|lower)[-\s]*(?:to|[~\-–—])[-\s]*(?:middle|mid)|중하\s*[~\-–—]")
_INC_VERY_HIGH = re.compile(r"very[\s_-]*high|매우\s*높|아주\s*높|최상위|초고소득")
_INC_RULES = (
    ("INC:HIGH", re.compile(r"\bhigh\b|고소득|높")),
    ("INC:MIDDLE", re.compile(r"\bmiddle\b|\bmedium\b|\bmid\b|\baverage\b|중간|중산|보통|평균")),
    ("INC:LOW", re.compile(r"\blow\b|저소득|낮")),
)


def _inc(text: str) -> list[str]:
    """ "very high"가 있으면 그 안의 "high"는 따로 세지 않는다. 급여 금액만 있는 값은 해석하지 않는다 (D1)."""
    if _INC_SPAN.search(text):
        return []                          # "평균 이하~평균 수준"처럼 두 등급에 걸친 표현은 정하지 않는다
    text = _INC_NO_NODE.sub(" ", text)
    if _INC_VERY_HIGH.search(text):
        return ["INC:VERY_HIGH"]
    return [node for node, pattern in _INC_RULES if pattern.search(text)]


# REL: 부정·과거 배우자 표현을 먼저 처리한 뒤 나머지에서 상태 단어를 찾는다.
#   "never married", "결혼하지 않았다" → SINGLE
#   "남편과 사별", "wife passed away"  → WIDOWED
#   "배우자 없음", "ex-wife", "전 남편", "결혼 예정", "연애 안 함" → 어느 범주인지 정할 수 없어 읽지 않는다
_REL_NEVER_MARRIED = re.compile(
    r"never (?:been )?married|not married|\bunmarried\b|미혼"
    r"|결혼(?:을|은|도)?\s*(?:하지\s*않|안\s*(?:함|했|한)|한\s*적(?:이|은)?\s*없)")
_REL_SPOUSE_DIED = re.compile(
    r"(?:husband|wife|spouse|남편|아내|배우자)[^.,;]{0,20}?(?:passed away|died|사망|세상을 떠|돌아가|사별)|사별|\bwidow")
_REL_NO_NODE = re.compile(
    r"\bno (?:spouse|husband|wife|girlfriend|boyfriend|partner)\b|(?:배우자|남편|아내|부인)\s*(?:가|는)?\s*없"
    r"|\bex[-\s]?(?:wife|husband|spouse|girlfriend|boyfriend)|former (?:wife|husband|spouse)|전\s*(?:남편|아내|부인|처)"
    r"|결혼\s*(?:예정|계획|준비)|(?:planning|plans|going) to (?:get married|marry)"
    r"|연애(?:를|는)?\s*(?:안|하지\s*않)|(?:남자|여자)\s*친구\s*(?:가|는)?\s*없|not (?:dating|in a relationship)")
_REL_RULES = (
    ("REL:SINGLE", re.compile(r"싱글|독신|\bsingle\b")),
    ("REL:MARRIED", re.compile(r"기혼|결혼|배우자|남편|아내|\bmarried\b|\bspouse\b|\bhusband\b|\bwife\b")),
    ("REL:ENGAGED", re.compile(r"약혼|\bengaged\b|fianc")),
    ("REL:IN_RELATIONSHIP", re.compile(r"연애|교제|사귀|여자\s*친구|남자\s*친구|in (?:a )?relationship|\bdating\b|girlfriend|boyfriend")),
    ("REL:DIVORCED", re.compile(r"이혼|\bdivorced\b")),
)


def _rel(text: str) -> list[str]:
    found: set[str] = set()
    for node, pattern in (("REL:SINGLE", _REL_NEVER_MARRIED), ("REL:WIDOWED", _REL_SPOUSE_DIED)):
        if pattern.search(text):
            found.add(node)
            text = pattern.sub(" ", text)
    text = _REL_NO_NODE.sub(" ", text)
    found.update(node for node, pattern in _REL_RULES if pattern.search(text))
    return sorted(found)


_SEX_RULES = (
    ("SEX:FEMALE", re.compile(r"\bfemale\b|\bwoman\b|\bwomen\b|여성|여자")),
    ("SEX:MALE", re.compile(r"\bmale\b|\bman\b|\bmen\b|남성|남자")),
)


def _sex(text: str) -> list[str]:
    return [node for node, pattern in _SEX_RULES if pattern.search(text)]
