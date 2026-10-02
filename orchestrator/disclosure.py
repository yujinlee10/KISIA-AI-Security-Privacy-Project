# 공개된 QI 기록과 그로부터 만드는 값들
#
# State는 두 군데에 있다.
#   risk_defender PrivacyState (RiskSession) : 후보 수 k 계산에 쓰는 State. MAPPED된 5개 QI만 들어간다.
#   DisclosureLedger (이 파일)               : "공개된 응답에 어떤 QI가 나왔는가"의 기록. 8개 QI 전부.
#
# 둘을 나눈 이유
#   SEX·INC·POB나 정규화에 실패한 값은 k 계산에 넣을 수 없지만(D1 Stage 5: PENDING·UNMAPPED는 matcher 전달 금지),
#   공격자 입장에서는 이미 받아낸 정보다. 이것을 공격 선택에 반영하지 않으면 Fuzzer가 같은 QI를
#   "아직 모르는 정보"(O1)로 계속 다시 묻는다. 팀 합의("추출된 QI는 일단 공개된 것으로 기록")에 따라
#   공격 선택용 view에는 공개된 QI를 모두 넣고, k 계산은 risk_defender State만 쓴다.
#
# 여기 들어오는 값은 전부 공개된 응답(released_response)에서 나온 것이다. raw candidate 값은 들어오지 않는다.

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from core.schemas import QI_TYPES
from normalizer.normalizer import NormalizedObservation
from risk_defender.schema import is_more_specific

EMPTY_STATE_ID = "S-EMPTY"


class DisclosureLedger:
    """세션 하나에서 공개된 QI를 속성별로 기록한다."""

    def __init__(self) -> None:
        self._loose: dict[str, str] = {}   # k 계산에 못 들어간 QI의 공개 값 (속성 → 표현)

    def record(self, released: Sequence[NormalizedObservation], committed_ids: Sequence[str]) -> None:
        """
        input : released (공개된 응답에서 뽑아 정규화한 관찰), committed_ids (그중 State에 commit된 관찰 id)
        output : 없음

        commit되지 않은 관찰만 따로 적어 둔다. 같은 속성이 다시 나오면 처음 값을 유지한다.
        """
        committed = set(committed_ids)
        for obs in released:
            if obs.observation_id in committed:
                continue
            self._loose.setdefault(obs.attribute, obs.value_en or obs.raw_value)

    def view(self, committed_qis: Mapping[str, Any]) -> dict[str, str]:
        """
        input : committed_qis (RiskSession.committed_qis)
        output : 공격 선택용 {QI → 공개된 값}. 같은 QI가 양쪽에 있으면 commit된 값을 쓴다. QI_TYPES 순서
        """
        merged = {**self._loose, **{qi: str(value) for qi, value in committed_qis.items()}}
        return {qi: merged[qi] for qi in QI_TYPES if qi in merged}


def state_id(disclosed: Mapping[str, str]) -> str:
    """
    input : disclosed (DisclosureLedger.view 결과)
    output : 같은 공개 상태면 같은 문자열이 나오는 짧은 ID (evaluation 로그 state_id)
    """
    if not disclosed:
        return EMPTY_STATE_ID
    key = "|".join(f"{qi}={disclosed[qi]}" for qi in QI_TYPES if qi in disclosed)
    return "S-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def qi_relation(
    target_qi: str,
    before: Mapping[str, str],
    after: Mapping[str, str],
    released: Sequence[NormalizedObservation],
) -> str:
    """
    input : target_qi (이번 턴 Seed가 노린 QI), before·after (턴 전후 공개 상태), released (이번 턴 공개 관찰)
    output : evaluation VALID_QI_RELATIONS 중 하나

      NEW          : 처음 공개됨
      DESCENDANT   : 더 구체적인 값으로 바뀜
      SAME         : 변화 없음 (이번 턴에 그 QI가 안 나왔거나 같은 값)
      ANCESTOR     : 이미 공개된 값보다 덜 구체적인 값이 나옴 (State는 그대로)
      INCOMPATIBLE : 이미 공개된 값과 양립하지 않는 값이 나옴 (State는 그대로, 충돌 기록)
    구체성 비교는 risk_defender.schema.is_more_specific을 그대로 쓴다.
    """
    old, new = before.get(target_qi), after.get(target_qi)
    if old is None:
        return "NEW" if new is not None else "SAME"
    if new != old:
        return "DESCENDANT"

    for obs in released:
        if obs.attribute != target_qi or not obs.is_mapped or obs.canonical_value == old:
            continue
        if is_more_specific(target_qi, old, obs.canonical_value):
            return "ANCESTOR"
        return "INCOMPATIBLE"
    return "SAME"
