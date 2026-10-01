# 여러 모듈이 같은 뜻으로 써야 하는 QI 공통 정의
#
# QI 이름·순서는 D1 Part III 고정값과 evaluation_v1/log_schema_v1.py(이정민 파트)의 VALID_QI와 같다.
#   AGE, SEX, LOC, OCCP, EDU, REL, INC, POB
# risk_defender/schema.py(이유진 파트)의 DEFAULT_QI_ORDER는 이 중 5개(AGE, LOC, OCCP, EDU, REL)만 쓴다.
# 나머지 3개(SEX, INC, POB)는 추출은 하되, risk_defender로 넘길 때 Part 4 어댑터에서 처리한다.
#
# 이 파일은 값(상수·열거형)만 둔다. 동작 로직을 넣지 않아 어느 모듈이 import해도 부작용이 없다.

from __future__ import annotations

from enum import Enum


# 8개 QI. 순서도 계약이다 (로그 컬럼·StateKey 정렬에 쓰인다).
QI_TYPES: tuple[str, ...] = ("AGE", "SEX", "LOC", "OCCP", "EDU", "REL", "INC", "POB")
QI_TYPE_SET: frozenset[str] = frozenset(QI_TYPES)

# risk_defender가 현재 후보 수 k 계산에 쓰는 QI (risk_defender/schema.py DEFAULT_QI_ORDER와 같은 집합)
RISK_DEFENDER_QI_TYPES: frozenset[str] = frozenset({"AGE", "LOC", "OCCP", "EDU", "REL"})

# confidence는 1~5 정수 순서척도다. 확률로 해석하지 않는다 (D1 Part III).
CONFIDENCE_MIN = 1
CONFIDENCE_MAX = 5


class InferenceMode(str, Enum):
    """
    EXPLICIT
    → 응답이 값을 직접 말함 ("취리히에 살고 있습니다")
    CONTEXTUAL
    → 응답이 다른 단서로부터 추론해 제시함 ("호수 근처 출퇴근 언급으로 보아 취리히로 보입니다")
    """
    EXPLICIT = "EXPLICIT"
    CONTEXTUAL = "CONTEXTUAL"
