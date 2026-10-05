# 후보 수 k 계산용 색인
#
# node마다 "이 node이거나 그 아래 node에 속한 사람들"을 비트 집합(정수 하나)으로 미리 만들어 둔다.
# State가 {LOC: 스위스, AGE: 30대}이면 두 비트 집합의 교집합에서 켜진 비트 수가 k다.
# 30만 명 기준으로도 한 번 세는 데 수십 마이크로초라, Defender가 일반화 후보를 여러 개 시험해도 부담이 없다.
#
# 부모 집합은 자식 집합의 합집합이므로 k(부모) >= k(자식)이 구조적으로 성립한다 (D1 Stage 3 검증 규칙).

from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.schemas import QI_TYPES
from ontology.builder import PROFILE_FIELD_BY_QI, build_ontology
from ontology.isco import OccupationTable, load_occupation_table
from ontology.mapper import AttributeReport, MappingReport, ProfileMapper
from ontology.model import Ontology
from ontology.parsing import Parser


class PopulationIndex:
    """
    기준 집단 하나에 대한 색인. run 전체에서 하나만 만들어 세션들이 함께 쓴다 (읽기 전용).

    ontology / parser → 이 색인이 쓴 온톨로지와 해석 규칙 (normalizer도 같은 것을 써야 한다)
    report            → 기준 집단 매핑 보고
    size              → 기준 집단 인원
    """

    def __init__(self, ontology: Ontology, parser: Parser, profiles: Sequence[Mapping[str, Any]]) -> None:
        self.ontology = ontology
        self.parser = parser
        self.size = len(profiles)

        mapper = ProfileMapper(ontology, parser)
        members: dict[str, bytearray] = {}
        reports = {attribute: AttributeReport() for attribute in QI_TYPES}
        width = (self.size + 7) // 8

        for position, profile in enumerate(profiles):
            for attribute in QI_TYPES:
                value = profile.get(PROFILE_FIELD_BY_QI[attribute])
                if value is None or value == "":
                    continue
                report = reports[attribute]
                report.total += 1
                node_id = mapper.node_for(attribute, value)
                if node_id is None:
                    key = str(value)
                    report.unmapped_values[key] = report.unmapped_values.get(key, 0) + 1
                    continue
                report.mapped += 1
                level = ontology.node(node_id).level
                report.by_level[level] = report.by_level.get(level, 0) + 1
                bits = members.get(node_id)
                if bits is None:
                    bits = members[node_id] = bytearray(width)
                bits[position >> 3] |= 1 << (position & 7)

        for attribute in QI_TYPES:
            reports[attribute].distinct_nodes = sum(
                1 for node_id in members if ontology.node(node_id).attribute == attribute)

        # 구체적인 node부터 부모로 합쳐 올라간다
        sets: dict[str, int] = {node_id: int.from_bytes(bits, "little") for node_id, bits in members.items()}
        for node in sorted(ontology.nodes(), key=lambda n: ontology.depth(n.node_id), reverse=True):
            own = sets.get(node.node_id)
            if own and node.parent_id is not None:
                sets[node.parent_id] = sets.get(node.parent_id, 0) | own

        self._sets = sets
        self._everyone = (1 << self.size) - 1
        self.report = MappingReport(self.size, reports)

    @classmethod
    def build(cls, profiles: Sequence[Mapping[str, Any]], *,
              occupation_table: OccupationTable | None = None) -> "PopulationIndex":
        """
        input : profiles (기준 집단 원본 profile 목록. 순서가 곧 내부 번호다)
        output : PopulationIndex (온톨로지·해석 규칙을 함께 만든다)
        """
        table = occupation_table or load_occupation_table()
        ontology = build_ontology(profiles, occupation_table=table)
        return cls(ontology, Parser(ontology, table), profiles)

    def node_count(self, node_id: str) -> int:
        """이 node(와 그 아래)에 속한 사람 수."""
        self.ontology.node(node_id)
        return self._sets.get(node_id, 0).bit_count()

    def count(self, constraints: Mapping[str, str]) -> int:
        """
        input : constraints ({QI → node_id}. State에 공개된 QI들)
        output : 모든 조건을 만족하는 사람 수 k. 조건이 없으면 전체 인원

        뿌리(ANY)는 조건으로 세지 않는다 (정보가 없다는 뜻이므로).
        """
        current = self._everyone
        for node_id in constraints.values():
            if self.ontology.node(node_id).is_root:
                continue
            current &= self._sets.get(node_id, 0)
            if not current:
                return 0
        return current.bit_count()
