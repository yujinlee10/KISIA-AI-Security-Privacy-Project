# 기준 집단 profile → QI별 node (P_ref 정규화, D1 Stage 3.2)
#
# 실행 전에 기준 집단 전원의 QI 값을 node로 바꿔 둔다. 실행 중 추출된 값과 같은 Parser 규칙을 쓴다.
# 풀지 못한 값(UNMAPPED·모호)은 버리지 않고 MappingReport에 남긴다.
#   풀지 못한 사람은 그 QI에 조건이 걸리면 후보에서 빠진다. 그래서 k가 실제보다 작게 나올 수 있다.
#   D1은 Main 전에 기준 집단을 100% 정규화하라고 한다. 보고서의 unmapped가 0이어야 그 조건을 만족한다.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from core.schemas import QI_TYPES
from ontology.builder import PROFILE_FIELD_BY_QI, age_exact_id
from ontology.model import Ontology
from ontology.parsing import Parser


@dataclass
class AttributeReport:
    """
    total / mapped  → 값이 있었던 사람 수 / node로 풀린 사람 수
    by_level        → 단계별 사람 수 (예: {"unit": 250, "sub_major": 30, ...})
    unmapped_values → 풀지 못한 원본 값 → 사람 수
    distinct_nodes  → 쓰인 node 수
    """
    total: int = 0
    mapped: int = 0
    by_level: dict[str, int] = field(default_factory=dict)
    unmapped_values: dict[str, int] = field(default_factory=dict)
    distinct_nodes: int = 0

    @property
    def coverage(self) -> float:
        return self.mapped / self.total if self.total else 1.0


@dataclass
class MappingReport:
    population_size: int
    attributes: dict[str, AttributeReport]

    @property
    def fully_mapped(self) -> bool:
        return all(report.mapped == report.total for report in self.attributes.values())

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "population_size": self.population_size,
            "fully_mapped": self.fully_mapped,
            "attributes": {
                attribute: {
                    "total": report.total,
                    "mapped": report.mapped,
                    "coverage": round(report.coverage, 6),
                    "distinct_nodes": report.distinct_nodes,
                    "by_level": dict(sorted(report.by_level.items())),
                    "unmapped_values": dict(sorted(report.unmapped_values.items(), key=lambda kv: -kv[1])[:20]),
                }
                for attribute, report in self.attributes.items()
            },
        }


class ProfileMapper:
    """같은 원본 값은 한 번만 해석한다 (30만 명이어도 서로 다른 값은 수백 개뿐이다)."""

    def __init__(self, ontology: Ontology, parser: Parser) -> None:
        self._ontology = ontology
        self._parser = parser
        self._cache: dict[tuple[str, Any], str | None] = {}

    def node_for(self, attribute: str, value: Any) -> str | None:
        """
        input : attribute, value (profile의 원본 값)
        output : node_id. 풀지 못했거나 모호하면 None
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        key = (attribute, value)
        if key not in self._cache:
            self._cache[key] = self._resolve(attribute, value)
        return self._cache[key]

    def map_profile(self, profile: Mapping[str, Any]) -> dict[str, str]:
        """
        input : profile (SynthPAI 형식: age, sex, city_country, …)
        output : {QI → node_id} (풀린 QI만)
        """
        mapped: dict[str, str] = {}
        for attribute in QI_TYPES:
            node_id = self.node_for(attribute, profile.get(PROFILE_FIELD_BY_QI[attribute]))
            if node_id is not None:
                mapped[attribute] = node_id
        return mapped

    def _resolve(self, attribute: str, value: Any) -> str | None:
        if attribute == "AGE" and isinstance(value, (int, float)) and not isinstance(value, bool):
            node_id = age_exact_id(int(value))
            return node_id if node_id in self._ontology else None
        return self._parser.parse(attribute, str(value)).node_id
