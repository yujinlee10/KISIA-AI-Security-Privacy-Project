# adapters 패키지: 팀원 모듈을 통합 파이프라인에 연결하는 어댑터
#
#   risk_adapter.py      : risk_defender(이유진 파트) — 기준 집단 적재, 후보 수 k, Defender, State commit
#   prepare_reference.py : 기준 집단 데이터 준비 (원본 300명 다운로드 + 합성 30만 명 생성)
#   fuzzer_adapter.py    : fuzzer(한재이 파트) — k 피드백 → 변형 전략 → Mutation LLM으로 다음 질문
#
# 원칙
#   - 팀원 코드는 수정하지 않고 import해서 그대로 호출한다. 판정·일반화 로직을 여기서 다시 만들지 않는다.
#   - 우리 쪽 객체(NormalizedObservation)와 팀원 쪽 객체(risk_defender QIObservation) 사이 변환만 담당한다.
#   - 팀원 코드가 바뀌면 이 어댑터만 고치면 되도록, 팀원 모듈 import는 이 패키지 안에만 둔다.
