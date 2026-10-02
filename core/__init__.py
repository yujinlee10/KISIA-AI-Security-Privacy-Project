# core 패키지: 모든 연구 모듈(target_llm, qi_extractor, orchestrator, adapters)이 공통으로 의존하는 계약 모음
#
# 의존 방향 규칙
#   core  ←  target_llm / qi_extractor / adapters / orchestrator
#   core는 다른 프로젝트 패키지(fuzzer, risk_defender, evaluation_v1 등)를 import하지 않는다.
#   → core만 따로 떼어 테스트할 수 있고, 팀원 모듈이 바뀌어도 core는 영향을 받지 않는다.
#
# 현재 포함된 모듈
#   config.py      : .env를 읽어 불변 Settings 객체 제공
#   llm_client.py  : monogpt(Anthropic Messages 호환) 공통 호출 클라이언트
