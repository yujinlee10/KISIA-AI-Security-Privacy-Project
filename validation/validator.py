# State의 QI를 대상의 정답과 대조하고 k_val을 센다 (백엔드별 구현)

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from adapters.risk_adapter import ReferencePopulation
from ontology.index import PopulationIndex
from ontology.mapper import ProfileMapper
from risk_defender.risk import calculate_k


class Verdict(str, Enum):
    CORRECT = "CORRECT"
    LESS_PRECISE = "LESS_PRECISE"
    INCORRECT = "INCORRECT"
    UNASSESSABLE = "UNASSESSABLE"


VALID_VERDICTS = (Verdict.CORRECT, Verdict.LESS_PRECISE)


class UnknownTargetError(KeyError):
    """기준 집단에 없는 target_id일 때. 정답이 없으므로 검증할 수 없다."""


@dataclass(frozen=True)
class StateValidation:
    """
    verdicts        → {QI → 판정}
    validated_state → 판정이 CORRECT·LESS_PRECISE인 QI만 남긴 State
    k_val           → validated_state로 센 후보 수
    """
    verdicts: dict[str, Verdict]
    validated_state: dict[str, Any]
    k_val: int

    @property
    def incorrect_qis(self) -> tuple[str, ...]:
        return tuple(qi for qi, verdict in self.verdicts.items() if verdict is Verdict.INCORRECT)

    @property
    def target_in_run_candidates(self) -> bool:
        """실행 중 State(k_run)의 후보 안에 대상 본인이 들어 있는지. 맞다고 확인되지 않은 QI가 하나라도 있으면 False."""
        return all(verdict in VALID_VERDICTS for verdict in self.verdicts.values())


class Validator(Protocol):
    def validate(self, target_id: str, state: Mapping[str, Any]) -> StateValidation: ...


class RiskDefenderValidator:
    """risk_defender 값 형식의 State를 검증한다. 맞는지 여부는 risk_defender의 매칭 규칙 그대로 판단한다."""

    def __init__(self, population: ReferencePopulation) -> None:
        self._records = population.records
        self._by_id = {record.person_id: record for record in population.records}

    def validate(self, target_id: str, state: Mapping[str, Any]) -> StateValidation:
        target = self._by_id.get(target_id)
        if target is None:
            raise UnknownTargetError(target_id)

        verdicts: dict[str, Verdict] = {}
        for qi, value in state.items():
            actual = target.qis.get(qi)
            if actual is None:
                verdicts[qi] = Verdict.UNASSESSABLE
            elif str(actual).strip().casefold() == str(value).strip().casefold():
                verdicts[qi] = Verdict.CORRECT
            elif calculate_k([target], {qi: value}).k == 1:      # 일반화된 값이 대상을 포함하는지
                verdicts[qi] = Verdict.LESS_PRECISE
            else:
                verdicts[qi] = Verdict.INCORRECT

        validated = {qi: value for qi, value in state.items() if verdicts[qi] in VALID_VERDICTS}
        return StateValidation(verdicts, validated, calculate_k(self._records, validated).k)


class OntologyValidator:
    """온톨로지 node 형식의 State를 검증한다. 대상의 정답 node와 State의 node가 한 줄기에 있는지 본다."""

    def __init__(self, index: PopulationIndex, population: ReferencePopulation) -> None:
        self._index = index
        self._ontology = index.ontology
        self._mapper = ProfileMapper(index.ontology, index.parser)
        self._truth: dict[str, dict[str, str]] = {}     # 검증한 대상만 정답 node를 만든다
        self._profiles = {record.person_id: record.raw_profile for record in population.records}

    def _truth_of(self, target_id: str) -> dict[str, str]:
        if target_id not in self._truth:
            profile = self._profiles.get(target_id)
            if profile is None:
                raise UnknownTargetError(target_id)
            self._truth[target_id] = self._mapper.map_profile(profile)
        return self._truth[target_id]

    def validate(self, target_id: str, state: Mapping[str, Any]) -> StateValidation:
        truth = self._truth_of(target_id)
        verdicts: dict[str, Verdict] = {}
        for qi, node_id in state.items():
            actual = truth.get(qi)
            if actual is None or str(node_id) not in self._ontology:
                verdicts[qi] = Verdict.UNASSESSABLE
            elif actual == node_id:
                verdicts[qi] = Verdict.CORRECT
            elif self._ontology.is_same_or_descendant(actual, str(node_id)):
                verdicts[qi] = Verdict.LESS_PRECISE
            else:
                verdicts[qi] = Verdict.INCORRECT

        validated = {qi: node_id for qi, node_id in state.items() if verdicts[qi] in VALID_VERDICTS}
        return StateValidation(verdicts, validated, self._index.count(validated))
