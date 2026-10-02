# RawObservation → NormalizedObservation (MAPPED / PENDING / UNMAPPED)
#
# 판정 순서
#   1) risk_defender가 다루지 않는 QI(SEX·INC·POB)는 UNMAPPED(OUT_OF_RISK_SCOPE)
#      → 추출 기록·Coverage 집계에는 남지만 후보 수 계산에는 들어가지 않는다.
#   2) QI별 규칙 또는 기준 집단 어휘로 후보 값을 만든다
#   3) 후보 0개 → UNMAPPED / 2개 이상 → PENDING / 1개 → 기준 집단에 있으면 MAPPED, 없으면 UNMAPPED
#
# MAPPED 값만 State commit 후보가 된다 (commit 여부는 risk 어댑터의 τ_commit 기준이 마저 정한다).
# PENDING·UNMAPPED는 버리지 않고 이유와 함께 기록한다 (D1 Stage 3: mapping status + reason).

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from core.schemas import RISK_DEFENDER_QI_TYPES
from normalizer.rules import (
    GENERALIZED_NODES,
    age_candidates,
    clean_free_text,
    edu_candidates,
    rel_candidates,
)
from normalizer.vocabulary import ReferenceVocabulary, city_key, country_key
from qi_extractor.schema import RawObservation


class MappingStatus(str, Enum):
    MAPPED = "MAPPED"        # 기준 집단 값 하나로 확정
    PENDING = "PENDING"      # 후보가 여럿이라 확정 못 함 (추측 금지)
    UNMAPPED = "UNMAPPED"    # 해석 못 했거나, 기준 집단에 없는 값


class MappingReason(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"                # 기준 집단 표기와 그대로 일치 (대소문자 무시)
    RULE_MATCH = "RULE_MATCH"                  # 규칙으로 해석 후 기준 집단에서 확인
    CITY_MATCH = "CITY_MATCH"                  # 도시 이름으로 "City, Country" 확정
    COUNTRY_MATCH = "COUNTRY_MATCH"            # 국가 수준 값으로 확정
    GENERALIZED_NODE = "GENERALIZED_NODE"      # adult·higher_education 같은 일반화 노드
    AMBIGUOUS = "AMBIGUOUS"                    # 후보가 둘 이상
    NOT_IN_REFERENCE = "NOT_IN_REFERENCE"      # 해석은 됐지만 기준 집단에 없는 값 (k=0 방지)
    NO_RULE = "NO_RULE"                        # 해석 규칙에 걸리지 않음
    OUT_OF_RISK_SCOPE = "OUT_OF_RISK_SCOPE"    # risk_defender가 다루지 않는 QI


@dataclass(frozen=True)
class NormalizedObservation:
    """
    observation_id / attribute / raw_value / value_en / confidence / source_turn
    → RawObservation에서 그대로 가져옴
    canonical_value
    → risk_defender 형식 값. MAPPED일 때만 있음
    status / reason
    → 판정과 그 이유
    candidates
    → PENDING일 때 경합한 후보들
    alternatives
    → MAPPED이지만 기준 집단에 같은 뜻의 다른 표기가 있을 때 그 표기들
      (예: "New York, United States"를 고르고 "New York, USA"가 남음).
      이 표기를 가진 사람은 후보 수에서 빠지므로, k가 작게 나오면 이 필드를 확인한다.
    """
    observation_id: str
    attribute: str
    raw_value: str
    value_en: str | None
    confidence: int
    source_turn: int
    status: MappingStatus
    reason: MappingReason
    canonical_value: str | None = None
    candidates: tuple[str, ...] = field(default_factory=tuple)
    alternatives: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_mapped(self) -> bool:
        return self.status is MappingStatus.MAPPED

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "attribute": self.attribute,
            "raw_value": self.raw_value,
            "value_en": self.value_en,
            "confidence": self.confidence,
            "source_turn": self.source_turn,
            "status": self.status.value,
            "reason": self.reason.value,
            "canonical_value": self.canonical_value,
            "candidates": list(self.candidates),
            "alternatives": list(self.alternatives),
        }


class Normalizer:
    """
    기준 집단 어휘를 주입받아 관찰값을 정규화한다. LLM을 호출하지 않으므로 같은 입력에 항상 같은 결과를 낸다.
    """

    def __init__(self, vocabulary: ReferenceVocabulary) -> None:
        self._vocab = vocabulary

    def normalize_all(self, observations: Iterable[RawObservation]) -> tuple[NormalizedObservation, ...]:
        """
        input : RawObservation 목록 (한 턴의 추출 결과)
        output : 같은 순서의 NormalizedObservation 튜플
        """
        return tuple(self.normalize(obs) for obs in observations)

    def normalize(self, obs: RawObservation) -> NormalizedObservation:
        """
        input : RawObservation 하나
        output : NormalizedObservation
        """
        if obs.attribute not in RISK_DEFENDER_QI_TYPES:
            return self._result(obs, MappingStatus.UNMAPPED, MappingReason.OUT_OF_RISK_SCOPE)

        if obs.attribute == "LOC":
            return self._normalize_loc(obs)
        if obs.attribute == "OCCP":
            return self._normalize_exact(obs)

        rule = {"AGE": age_candidates, "EDU": edu_candidates, "REL": rel_candidates}[obs.attribute]
        return self._decide_rule_candidates(obs, rule(obs.raw_value, obs.value_en))

    # ── 규칙 기반 (AGE·EDU·REL) ─────────────────────────

    def _decide_rule_candidates(self, obs: RawObservation, candidates: set[str]) -> NormalizedObservation:
        if not candidates:
            return self._result(obs, MappingStatus.UNMAPPED, MappingReason.NO_RULE)
        if len(candidates) > 1:
            return self._result(obs, MappingStatus.PENDING, MappingReason.AMBIGUOUS,
                                candidates=tuple(sorted(candidates)))

        (value,) = candidates
        if value in GENERALIZED_NODES.get(obs.attribute, ()):
            return self._result(obs, MappingStatus.MAPPED, MappingReason.GENERALIZED_NODE, canonical=value)

        # REL처럼 기준 집단에 같은 뜻의 표기가 갈려 있으면 (in a relationship / in relationship)
        # 대소문자·공백만 다른 표기를 모아 가장 많이 쓰인 것을 고르고 나머지를 기록한다.
        matches = self._vocab.find(obs.attribute, value) + self._spelling_variants(obs.attribute, value)
        chosen = self._vocab.most_frequent(obs.attribute, matches)
        if chosen is None:
            return self._result(obs, MappingStatus.UNMAPPED, MappingReason.NOT_IN_REFERENCE,
                                candidates=(value,))
        others = tuple(sorted(set(matches) - {chosen}))
        return self._result(obs, MappingStatus.MAPPED, MappingReason.RULE_MATCH,
                            canonical=chosen, alternatives=others)

    def _spelling_variants(self, attribute: str, value: str) -> tuple[str, ...]:
        """기준 데이터에서 확인된 표기 갈림. 지금은 REL 한 건뿐이다."""
        if attribute == "REL" and value == "in a relationship":
            return self._vocab.find("REL", "in relationship")
        return ()

    # ── OCCP: 기준 집단 표기와 그대로 일치할 때만 ─────────

    def _normalize_exact(self, obs: RawObservation) -> NormalizedObservation:
        """
        직업은 기준 집단에 자유 문자열 221종이 있다. "software developer"와 "software engineer"를
        같다고 보는 것은 직업 분류(ontology) 결정이라 여기서 추측하지 않는다. 일치할 때만 MAPPED.
        """
        for text in (obs.value_en, obs.raw_value):
            if not text:
                continue
            matches = self._vocab.find(obs.attribute, clean_free_text(text))
            if matches:
                # find()는 대소문자만 다른 표기("software engineer"/"Software engineer")를 함께 돌려준다.
                # risk_defender 매칭은 대소문자를 무시하므로 k에 영향이 없어 alternatives로 남기지 않는다.
                return self._result(obs, MappingStatus.MAPPED, MappingReason.EXACT_MATCH,
                                    canonical=matches[0])
        return self._result(obs, MappingStatus.UNMAPPED, MappingReason.NOT_IN_REFERENCE)

    # ── LOC: "City, Country" / 도시 / 국가 순으로 대조 ─────

    def _normalize_loc(self, obs: RawObservation) -> NormalizedObservation:
        for text in (obs.value_en, obs.raw_value):
            if not text or not text.strip():
                continue
            result = self._resolve_loc_text(obs, text)
            if result is not None:
                return result
        return self._result(obs, MappingStatus.UNMAPPED, MappingReason.NOT_IN_REFERENCE)

    def _resolve_loc_text(self, obs: RawObservation, text: str) -> NormalizedObservation | None:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return None

        # 1) "City, Country" 형태: 도시와 국가가 모두 맞는 표기
        if len(parts) >= 2:
            entries = self._vocab.loc_by_city.get(city_key(parts[0]), ())
            same_country = [full for ckey, full in entries if ckey == country_key(parts[-1])]
            if same_country:
                return self._pick_loc(obs, same_country, MappingReason.EXACT_MATCH)
            # 도시가 기준 집단에 없으면 국가 수준으로라도 확정한다
            return self._resolve_country(obs, parts[-1])

        # 2) 도시 이름만
        entries = self._vocab.loc_by_city.get(city_key(parts[0]), ())
        if entries:
            countries = {ckey for ckey, _ in entries}
            if len(countries) > 1:
                return self._result(obs, MappingStatus.PENDING, MappingReason.AMBIGUOUS,
                                    candidates=tuple(sorted(full for _, full in entries)))
            return self._pick_loc(obs, [full for _, full in entries], MappingReason.CITY_MATCH)

        # 3) 국가 이름만
        return self._resolve_country(obs, parts[0])

    def _resolve_country(self, obs: RawObservation, name: str) -> NormalizedObservation | None:
        """
        국가 수준 값은 risk_defender가 "X, 국가" 레코드와 매칭한다 (마지막 쉼표 뒤 문자열을 대소문자 무시 비교).
        기준 집단에 "United States"와 "USA"가 섞여 있으면 많이 쓰인 표기를 고르고 나머지를 기록한다.
        """
        spellings = self._vocab.country_spellings.get(country_key(name))
        if not spellings:
            return None
        ordered = sorted(spellings, key=lambda s: (-spellings[s], s))
        return self._result(obs, MappingStatus.MAPPED, MappingReason.COUNTRY_MATCH,
                            canonical=ordered[0], alternatives=tuple(ordered[1:]))

    def _pick_loc(self, obs: RawObservation, fulls: list[str], reason: MappingReason) -> NormalizedObservation:
        chosen = self._vocab.most_frequent("LOC", fulls)
        others = tuple(sorted(set(fulls) - {chosen}))
        return self._result(obs, MappingStatus.MAPPED, reason, canonical=chosen, alternatives=others)

    # ── 결과 조립 ────────────────────────────────────────

    @staticmethod
    def _result(
        obs: RawObservation,
        status: MappingStatus,
        reason: MappingReason,
        *,
        canonical: str | None = None,
        candidates: tuple[str, ...] = (),
        alternatives: tuple[str, ...] = (),
    ) -> NormalizedObservation:
        return NormalizedObservation(
            observation_id=obs.observation_id,
            attribute=obs.attribute,
            raw_value=obs.raw_value,
            value_en=obs.value_en,
            confidence=obs.confidence,
            source_turn=obs.source_turn,
            status=status,
            reason=reason,
            canonical_value=canonical,
            candidates=candidates,
            alternatives=alternatives,
        )
