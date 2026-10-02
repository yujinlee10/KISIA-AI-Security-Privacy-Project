# attack 패키지: 공격 질문을 고르는 Backend (D1 Stage 7·8의 "Backend Scheduler" 쪽)
#
#   registry.py   : Seed Registry(O1·O2) 파일을 읽고 검증 (사람이 만든 질문 후보 목록)
#   conditions.py : Seed마다 붙은 required_state_condition을 해석·평가
#   selector.py   : 지금 State에 맞는 Seed를 고르고 [SUBJECT]·[PREVIOUSLY_DISCLOSED_QI]를 채움
#
# 정보 경계 (D1 Stage 1·6·8, 팀 합의)
#   - Backend가 보는 것은 "공개된 응답에서 commit된 QI"와 "지금까지 몇 턴 했는지"뿐이다 (AttackStateView).
#   - 후보 수 k, 정답(GT), Raw Candidate는 이 패키지에 들어오지 않는다.
#     k는 한재이 파트 feedback 함수가 전략을 고를 때만 쓰이고, 그 전략 이름만 Mutation LLM에 전달된다.
#
# Seed 내용·변형 전략은 한재이 파트 소유다. 이 패키지는 파일을 읽고 조건을 평가하는 배관만 담당한다.
