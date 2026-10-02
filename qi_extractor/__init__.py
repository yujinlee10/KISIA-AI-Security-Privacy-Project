# qi_extractor 패키지: Target 응답에서 8개 QI를 근거와 함께 뽑아 검증한다 (D1 Stage 2)
#
# 처리 흐름
#   extractor.py : (query, response) 한 쌍을 LLM에 보내 JSON으로 관찰값을 받는다
#   evidence.py  : 근거 문구가 응답 원문에 실제로 있는지, 질문을 되풀이한 것뿐인지 검사
#   schema.py    : 통과한 관찰(RawObservation)과 거부된 관찰(RejectedObservation) 정의
#
# 하지 않는 일
#   - 정규화("취리히" → "Zurich, Switzerland")는 하지 않는다. Normalizer(Part 4) 책임이다.
#   - State commit 여부를 정하지 않는다. confidence 기준 τ_commit은 Pilot 후 freeze할 값이다.
#   - 과거 턴을 다시 추출하지 않는다. 누적은 State가 담당한다.
#   - 정답(GT)을 보지 않는다. 이 패키지는 evaluation 저장소를 import하지 않는다.
#
# 이름 주의
#   risk_defender/schema.py에 이미 QIObservation이 있다 (정규화된 값을 State에 넣는 입력).
#   혼동을 막기 위해 여기 출력은 RawObservation이라 부른다. Part 4 어댑터가 둘을 이어준다.
