# target_llm 패키지: 공격 대상이 되는 Target System (Controlled RAG)
#
# 처리 흐름
#   download.py   : SynthPAI comments 원본을 커밋 고정 URL에서 받아 해시 검증
#   corpus.py     : 원본에서 정답(profile)·라벨(reviews)을 떼어내고 persona별 글만 보관
#                   (D1 Stage 1의 Persona Knowledge Store)
#   retriever.py  : 지정한 persona의 글만 꺼내는 strict target filter 검색기
#   responder.py  : 검색 결과로 LLM을 호출해 raw_candidate_response 생성
#
# 경계 규칙 (D1 Stage 1, HANDOFF §4.1·§5)
#   - Target은 raw_candidate_response까지만 만든다. 공개(released) 여부는 이 패키지가 정하지 않는다.
#     공개 게이트(Defender, C0에서는 그대로 통과)는 orchestrator 단계의 책임이다.
#   - structured profile·GT는 이 패키지 어디에도 들어오지 않는다.
#   - 다른 persona의 글이 섞이면(contamination) 즉시 오류로 멈춘다.
