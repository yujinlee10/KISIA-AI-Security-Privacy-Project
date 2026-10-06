# ontology 패키지: 8개 QI의 표준 값(node)과 상하위 관계 (D1 Stage 3)
#
#   model.py      : Node·Ontology (node_id, parent_id, 조상·자손 조회)
#   tables.py     : EDU·INC·REL·SEX 값, 국가 표 (고정 표)
#   isco.py       : 직업 분류 ISCO-08 그룹 이름과 직업 표기 → ISCO 코드 대응표 적재
#   builder.py    : 위 표와 기준 집단의 도시 목록으로 Ontology를 만든다 (버전·해시 포함)
#   parsing.py    : 표현(한국어·영어) → node 해석 규칙. 기준 집단 매핑과 실행 중 정규화가 같은 규칙을 쓴다
#   mapper.py     : 기준 집단 profile → QI별 node (P_ref 정규화) + 매핑 보고
#   normalizer.py : 추출된 관찰 → node (MAPPED / PENDING / UNMAPPED)
#   index.py      : node별 해당 인원 집합을 미리 계산해 후보 수 k를 빠르게 센다
#   defender.py   : 공개 전 일반화 탐색 (D1 Stage 11: 한 단계씩 올리는 너비 우선 탐색, 하한 k_floor)
#   __main__.py   : python -m ontology  → 기준 집단 매핑 점검표 출력
#
# 계층 (D1 Stage 3.1 개발 기준)
#   AGE  : 정확한 나이 → 5년 구간 → 10년 구간 → ANY      (25–29 ⊂ 20–29처럼 같은 기준점으로 포갠다)
#   LOC  : 도시 → 국가 → ANY                              (주·도 단계 없음)
#   POB  : 출생 도시 → 출생 국가 → ANY
#   OCCP : ISCO-08 Unit(4자리) → Minor(3) → Sub-major(2) → Major(1) → ANY
#   EDU  : 박사·석사·학사 → Tertiary → ANY, 고졸 → ANY, 대학 재학 → ANY
#   INC  : low / middle / high / very_high → ANY
#   REL  : 6개 범주 → ANY
#   SEX  : male / female → ANY
#
# 원칙
#   - 정규화(같은 뜻을 한 node로)와 일반화(부모 node로 올리기)를 나눈다. 이 패키지의 normalizer는 정규화만 한다.
#   - 모호하면 추측하지 않고 PENDING, 규칙에 없으면 UNMAPPED.
#   - ANY는 계산용 뿌리다. 억제(suppress)된 QI를 ANY로 State에 넣지 않는다.
#   - 기준 집단 값이 어느 node인지는 실행 전에 한 번 정하고, 표의 해시로 버전을 남긴다.
#
# 이 패키지는 risk_defender(이유진 파트)를 바꾸지 않는다. adapters/ontology_risk.py가
# 같은 실행기에서 risk_defender 대신 꽂아 쓸 수 있는 두 번째 위험 계산 백엔드를 만든다.
#
# 검토가 필요한 부분 (배성윤 님 확인 대상, docs/ontology.md에 정리)
#   - data/occp_alias_v0.1.csv 의 직업 → ISCO 코드 대응 (기계 초안, confidence L/M 행)
#   - EDU: 재학 중·Diploma·MD·Law Degree를 어느 node로 볼지
#   - OCCP: 은퇴·분류 불가 직업의 처리 (ISCO 밖의 보조 node OCCP:X)
#   - 도시 목록을 GeoNames가 아니라 기준 집단에 나온 도시로 한정한 점
