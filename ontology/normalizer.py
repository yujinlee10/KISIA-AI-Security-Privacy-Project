# 추출된 관찰 → 온톨로지 node (MAPPED / PENDING / UNMAPPED)
#
# normalizer/normalizer.py(risk_defender 값 형식용)와 같은 결과 객체(NormalizedObservation)를 돌려준다.
# 차이는 canonical_value가 node_id라는 점과, 8개 QI를 모두 다룬다는 점이다.
#
# 이 정규화기는 기준 집단을 보지 않는다. "응답이 말한 값이 온톨로지의 어느 node인가"만 판단한다.
# 기준 집단에 그 node인 사람이 없으면 k가 0이 되는데(ZERO_MATCH), 그 처리는 위험 계산 쪽 몫이다.

from __future__ import annotations

from typing import Iterable

from normalizer.normalizer import MappingReason, MappingStatus, NormalizedObservation
from ontology.model import Ontology
from ontology.parsing import Parser
from qi_extractor.schema import RawObservation


class OntologyNormalizer:
    def __init__(self, ontology: Ontology, parser: Parser) -> None:
        self._ontology = ontology
        self._parser = parser

    def normalize_all(self, observations: Iterable[RawObservation]) -> tuple[NormalizedObservation, ...]:
        return tuple(self.normalize(obs) for obs in observations)

    def normalize(self, obs: RawObservation) -> NormalizedObservation:
        """
        input : RawObservation
        output : NormalizedObservation (MAPPED이면 canonical_value = node_id)
        """
        parsed = self._parser.parse(obs.attribute, obs.raw_value, obs.value_en)
        reason = MappingReason[parsed.reason]

        if parsed.node_id is not None:
            status, canonical, candidates = MappingStatus.MAPPED, parsed.node_id, ()
        elif parsed.node_ids:
            status, canonical, candidates = MappingStatus.PENDING, None, parsed.node_ids
        else:
            status, canonical, candidates = MappingStatus.UNMAPPED, None, ()

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
            candidates=tuple(candidates),
        )
