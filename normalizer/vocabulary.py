# 기준 집단(P_ref)에 실제로 존재하는 QI 값 목록
#
# 정규화 결과는 이 목록 안의 값이어야 의미가 있다. 목록 밖 값으로 후보를 세면 k=0이 된다.
# 기준 집단을 한 번 훑어 만들고(30만 명 기준 약 0.5초), run 동안 재사용한다.
#
# 입력은 risk_defender.loader.PersonaRecord처럼 `.qis` dict를 가진 객체면 된다.
# 이 파일은 risk_defender를 import하지 않는다 (모양만 맞으면 테스트용 객체도 받는다).
#
# 기준 데이터 표기 문제 (2026-10-01 SynthPAI 원본 300명 감사 결과)
#   같은 국가가 두 가지로 적혀 있다: "United States"/"USA", "United Arab Emirates"/"UAE".
#   그래서 국가 이름은 COUNTRY_ALIASES로 같은 키로 묶어서 찾는다.
#   찾은 뒤에는 기준 집단에서 가장 많이 쓰인 표기를 결과로 고르고, 나머지 표기는 alternatives로 기록한다.
#   (기준 집단 쪽 표기를 통일하는 것은 risk_defender 정규화의 일이라 여기서는 하지 않는다.)

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# 국가 표기 별칭 → 대표 키 (소문자). 실제 기준 데이터에서 확인된 것과 흔한 약어만 둔다.
COUNTRY_ALIASES: Mapping[str, str] = {
    "usa": "united states",
    "us": "united states",
    "u.s.": "united states",
    "u.s.a.": "united states",
    "united states of america": "united states",
    "america": "united states",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "great britain": "united kingdom",
    "uae": "united arab emirates",
}


def country_key(name: str) -> str:
    """국가 이름을 비교용 키로 바꾼다 ("USA" → "united states")."""
    lowered = " ".join(name.strip().lower().split())
    return COUNTRY_ALIASES.get(lowered, lowered)


def city_key(name: str) -> str:
    """도시 이름을 비교용 키로 바꾼다 (소문자, 공백 정리)."""
    return " ".join(name.strip().lower().split())


@dataclass(frozen=True)
class ReferenceVocabulary:
    """
    values
    → QI별 {기준 집단 값: 사람 수}
    lowered
    → QI별 {소문자 값: 원래 표기들} (대소문자 차이 흡수용)
    loc_by_city
    → 도시 키 → [(국가 키, "City, Country" 원래 표기), ...]
    country_spellings
    → 국가 키 → {국가 원래 표기: 사람 수}
    population_size
    → 어휘를 만든 기준 집단 크기 (로그 기록용)
    """
    values: Mapping[str, Mapping[str, int]]
    lowered: Mapping[str, Mapping[str, tuple[str, ...]]]
    loc_by_city: Mapping[str, tuple[tuple[str, str], ...]]
    country_spellings: Mapping[str, Mapping[str, int]]
    population_size: int

    @classmethod
    def from_population(cls, population: Iterable[Any]) -> "ReferenceVocabulary":
        """
        input : population (`.qis` dict를 가진 레코드들. 예: risk_defender PersonaRecord 목록)
        output : ReferenceVocabulary
        """
        counts: dict[str, Counter] = defaultdict(Counter)
        size = 0
        for record in population:
            size += 1
            for attribute, value in record.qis.items():
                if isinstance(value, str) and value.strip():
                    counts[attribute][value] += 1

        lowered: dict[str, dict[str, tuple[str, ...]]] = {}
        for attribute, counter in counts.items():
            groups: dict[str, list[str]] = defaultdict(list)
            for value in counter:
                groups[value.casefold()].append(value)
            lowered[attribute] = {key: tuple(items) for key, items in groups.items()}

        loc_by_city: dict[str, list[tuple[str, str]]] = defaultdict(list)
        country_spellings: dict[str, Counter] = defaultdict(Counter)
        for full, number in counts.get("LOC", Counter()).items():
            parts = [part.strip() for part in full.split(",") if part.strip()]
            if len(parts) < 2:
                continue
            ckey = country_key(parts[-1])
            loc_by_city[city_key(parts[0])].append((ckey, full))
            country_spellings[ckey][parts[-1]] += number

        return cls(
            values={attribute: dict(counter) for attribute, counter in counts.items()},
            lowered=lowered,
            loc_by_city={city: tuple(items) for city, items in loc_by_city.items()},
            country_spellings={ckey: dict(counter) for ckey, counter in country_spellings.items()},
            population_size=size,
        )

    def count(self, attribute: str, value: str) -> int:
        """기준 집단에서 이 값을 가진 사람 수 (없으면 0)."""
        return self.values.get(attribute, {}).get(value, 0)

    def find(self, attribute: str, text: str) -> tuple[str, ...]:
        """
        input : attribute, text
        output : 대소문자·공백 차이를 무시하고 일치하는 기준 집단 표기들 (많이 쓰인 순)
        """
        key = " ".join(text.strip().split()).casefold()
        matches = self.lowered.get(attribute, {}).get(key, ())
        return tuple(sorted(matches, key=lambda v: -self.count(attribute, v)))

    def most_frequent(self, attribute: str, candidates: Iterable[str]) -> str | None:
        """후보 중 기준 집단에서 가장 많이 쓰인 값. 동률이면 사전순으로 고정 (결과 재현성)."""
        present = [c for c in candidates if self.count(attribute, c) > 0]
        if not present:
            return None
        return sorted(present, key=lambda v: (-self.count(attribute, v), v))[0]
