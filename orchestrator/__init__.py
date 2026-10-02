# orchestrator 패키지: 공격 → 응답 → 추출 → 위험 판정 → 공개 → State 반영을 한 턴으로 묶어 반복 실행한다
#
#   disclosure.py   : 공개된 QI 기록(공격 선택용 view), State key, QI 관계 판정
#   rewriter.py     : Defender 판정(일반화·억제)대로 응답 문장을 고쳐 쓰는 LLM 호출
#   release_gate.py : 공개 게이트. 조건(C0/C1)에 따라 raw candidate를 그대로 내보낼지, 고쳐서 내보낼지 정한다
#   session.py      : 세션 한 개(target 1명 × 조건 1개)의 턴 루프
#   run_log.py      : 실행 기록 — evaluation(이정민 파트) CSV + trusted trace JSONL + run 설정
#   __main__.py     : python -m orchestrator --target pers1 --conditions C0 C1 --turns 5
#
# 한 턴의 순서 (D1 Part III §4, HANDOFF §4.1)
#   1) Fuzzer 어댑터가 공개된 정보만 보고 질문을 만든다
#   2) Target이 raw candidate를 만든다                       ← 공격자·State에 직접 전달 금지
#   3) raw candidate에서 QI를 추출·정규화한다                 ← 위험 계산용 (projected)
#   4) 공개 게이트: C0는 그대로 통과 / C1은 Defender 판정 → 재작성 → 재추출 → 하한 확인
#   5) 공개된 응답에서 나온 관찰만 State에 commit한다
#   6) commit 전후 k를 Fuzzer 어댑터에 돌려준다
#
# 조건
#   C0 : Defender 없음 (기준선)        C1 : 공개 전 Defender
#   C2·C3(경량 언러닝 규칙 주입)은 POC 이후 파트에서 추가한다.
