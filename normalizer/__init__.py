# normalizer 패키지: 추출된 raw 값을 기준 집단(P_ref)이 쓰는 값으로 바꾼다 (D1 Stage 3)
#
#   "취리히"(raw) / "Zurich"(value_en)  →  "Zurich, Switzerland"  (risk_defender LOC 값)
#   "30대 중반"                          →  "30s"                  (risk_defender AGE 값)
#
# 구성
#   vocabulary.py : 기준 집단에 실제로 있는 값 목록 (P_ref에서 한 번 만들어 재사용)
#   rules.py      : QI별 한국어·영어 표현 → 후보 값 규칙 (결정적, LLM 호출 없음)
#   normalizer.py : 규칙 + 어휘 대조 → MAPPED / PENDING / UNMAPPED 판정
#
# 원칙 (D1 Stage 3)
#   - 모호하면 추측하지 않고 PENDING. 기준 집단에 없는 값이면 UNMAPPED.
#     잘못 MAPPED 되면 후보 수 k가 틀린 사람들로 좁혀지거나 0(ZERO_MATCH)이 된다.
#   - 결과 값은 반드시 기준 집단에 실제로 존재하는 값(또는 risk_defender가 아는 일반화 노드)이어야 한다.
#   - 정규화(같은 뜻 통일)만 한다. 일반화(정밀도 낮추기)는 Defender의 일이다.
#
# 이 패키지는 risk_defender의 값 형식에 맞추는 다리 역할이다.
# 값 형식 자체(예: AGE를 10년 단위로 쓰는 것)는 이유진 파트의 결정을 그대로 따른다.
