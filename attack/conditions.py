# Seed의 required_state_condition 해석·평가
#
# 레지스트리에 실제로 쓰인 조건은 네 가지 술어를 AND로 이은 것뿐이다 (O1 72행·O2 45행 전수 확인).
#   "<QI> not active"                  : 그 QI가 아직 공개되지 않음        (O1 New-QI Acquisition)
#   "<QI> active"                      : 그 QI가 이미 공개됨              (O2 Specificity Refinement)
#   "active_node_has_descendant = true": 공개된 값보다 더 구체적인 값이 있음 (O2)
#   "prior_context_available = true"   : 앞선 대화가 있음                 (S4 Follow-up)
#
# O2 EDU 9행은 조건 문자열 안에 사람용 설명이 괄호로 붙어 있다.
#   "EDU active AND active_node_has_descendant = true (current Core ontology: typically TERTIARY_EDUCATION)"
# 괄호 부분은 떼어 ontology_note로 보관하고 조건 평가에는 쓰지 않는다.
#
# 모르는 술어가 나오면 레지스트리 로딩 단계에서 바로 실패한다 (조용히 '참'으로 넘기지 않는다).

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from core.schemas import QI_TYPE_SET
from normalizer.rules import GENERALIZED_NODES


class ConditionParseError(ValueError):
    """required_state_condition에 지원하지 않는 술어가 있을 때."""


class Predicate(str, Enum):
    QI_ACTIVE = "QI_ACTIVE"
    QI_NOT_ACTIVE = "QI_NOT_ACTIVE"
    ACTIVE_NODE_HAS_DESCENDANT = "ACTIVE_NODE_HAS_DESCENDANT"
    PRIOR_CONTEXT_AVAILABLE = "PRIOR_CONTEXT_AVAILABLE"


@dataclass(frozen=True)
class Condition:
    """
    predicate
    → 술어 종류
    qi
    → QI_ACTIVE / QI_NOT_ACTIVE일 때 대상 QI. 나머지 술어는 None (Seed의 target_qi를 기준으로 평가)
    """
    predicate: Predicate
    qi: str | None = None


_PAREN_NOTE = re.compile(r"\(([^)]*)\)")
_QI_ACTIVE = re.compile(r"^([A-Z]+)\s+active$")
_QI_NOT_ACTIVE = re.compile(r"^([A-Z]+)\s+not\s+active$")
_HAS_DESCENDANT = re.compile(r"^active_node_has_descendant\s*=\s*true$", re.IGNORECASE)
_PRIOR_CONTEXT = re.compile(r"^prior_context_available\s*=\s*true$", re.IGNORECASE)


def parse_conditions(text: str) -> tuple[tuple[Condition, ...], str | None]:
    """
    input : required_state_condition 원문
    output : (조건 튜플, 괄호 설명 또는 None)
    """
    notes = [note.strip() for note in _PAREN_NOTE.findall(text) if note.strip()]
    stripped = _PAREN_NOTE.sub(" ", text)

    conditions: list[Condition] = []
    for part in re.split(r"\s+AND\s+", stripped.strip()):
        clause = " ".join(part.split())
        if not clause:
            continue
        if match := _QI_NOT_ACTIVE.match(clause):
            conditions.append(Condition(Predicate.QI_NOT_ACTIVE, _check_qi(match.group(1), text)))
        elif match := _QI_ACTIVE.match(clause):
            conditions.append(Condition(Predicate.QI_ACTIVE, _check_qi(match.group(1), text)))
        elif _HAS_DESCENDANT.match(clause):
            conditions.append(Condition(Predicate.ACTIVE_NODE_HAS_DESCENDANT))
        elif _PRIOR_CONTEXT.match(clause):
            conditions.append(Condition(Predicate.PRIOR_CONTEXT_AVAILABLE))
        else:
            raise ConditionParseError(f"지원하지 않는 조건: {clause!r} (원문: {text!r})")

    if not conditions:
        raise ConditionParseError(f"조건이 비어 있습니다: {text!r}")
    return tuple(conditions), ("; ".join(notes) or None)


def _check_qi(qi: str, text: str) -> str:
    if qi not in QI_TYPE_SET:
        raise ConditionParseError(f"알 수 없는 QI {qi!r} (원문: {text!r})")
    return qi


def has_descendant(attribute: str, value: str) -> bool:
    """
    input : attribute, value (State에 commit된 값, risk_defender 형식)
    output : 이 값보다 더 구체적인 값이 risk_defender 값 체계에 있는지

    risk_defender가 이해하는 계층만 본다 (risk_defender/risk.py _matches_qi 기준).
      LOC  : 국가 수준("Switzerland")이면 아래에 도시가 있다 / "City, Country"는 더 내려갈 곳이 없다
      AGE  : adult·senior·young 아래에 10년 단위가 있다 / "30s"는 risk_defender가 더 세분하지 않는다
      EDU  : higher_education 등 아래에 학위가 있다
      REL  : partnered 등 아래에 구체 상태가 있다
      OCCP : 자유 문자열이라 계층이 없다
    D1은 AGE를 exact → 5년 → 10년으로 둔다. risk_defender가 10년 단위로 통일하므로 여기서도 그에 맞춘다.
    """
    if attribute == "LOC":
        return "," not in value
    return value in GENERALIZED_NODES.get(attribute, frozenset())


def evaluate(
    conditions: tuple[Condition, ...],
    target_qi: str,
    committed_qis: Mapping[str, str],
    prior_turns: int,
) -> bool:
    """
    input : conditions, target_qi (Seed가 노리는 QI), committed_qis (공개·commit된 QI), prior_turns (앞선 턴 수)
    output : 모든 조건을 만족하면 True
    """
    for condition in conditions:
        if condition.predicate is Predicate.QI_ACTIVE:
            ok = condition.qi in committed_qis
        elif condition.predicate is Predicate.QI_NOT_ACTIVE:
            ok = condition.qi not in committed_qis
        elif condition.predicate is Predicate.ACTIVE_NODE_HAS_DESCENDANT:
            value = committed_qis.get(target_qi)
            ok = value is not None and has_descendant(target_qi, str(value))
        else:  # PRIOR_CONTEXT_AVAILABLE
            ok = prior_turns > 0
        if not ok:
            return False
    return True
