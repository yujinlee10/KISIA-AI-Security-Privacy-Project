# core/schemas.py 단위 테스트 — 팀원 모듈과 맞춰야 하는 QI 계약 고정값 확인

from __future__ import annotations

from core.schemas import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    QI_TYPE_SET,
    QI_TYPES,
    RISK_DEFENDER_QI_TYPES,
    InferenceMode,
)


def test_qi_types_match_d1_and_evaluation_order():
    """evaluation_v1/log_schema_v1.py의 VALID_QI와 같은 이름·순서여야 한다."""
    assert QI_TYPES == ("AGE", "SEX", "LOC", "OCCP", "EDU", "REL", "INC", "POB")
    assert QI_TYPE_SET == frozenset(QI_TYPES)


def test_risk_defender_qi_types_are_a_subset():
    """risk_defender/schema.py의 DEFAULT_QI_ORDER 5개."""
    assert RISK_DEFENDER_QI_TYPES == {"AGE", "LOC", "OCCP", "EDU", "REL"}
    assert RISK_DEFENDER_QI_TYPES <= QI_TYPE_SET


def test_confidence_scale_is_one_to_five():
    assert (CONFIDENCE_MIN, CONFIDENCE_MAX) == (1, 5)


def test_inference_modes():
    assert {m.value for m in InferenceMode} == {"EXPLICIT", "CONTEXTUAL"}
