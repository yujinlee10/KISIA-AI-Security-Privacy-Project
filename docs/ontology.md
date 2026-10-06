# 온톨로지 (D1 Stage 3) 구현 정리

`ontology/` 패키지는 개발 의사결정 요약보고서 v1.0의 Stage 3(Normalization · Ontology · Generalization Hierarchy)를 구현한 것입니다. `risk_defender`를 고치지 않고, 같은 실행기에서 `--backend ontology`로 바꿔 쓸 수 있는 두 번째 위험 계산 방식으로 들어갑니다.

## 계층

| QI | 계층 (구체 → 넓게) | node_id 예 |
| --- | --- | --- |
| AGE | 정확한 나이 → 5년 구간 → 10년 구간 → ANY | `AGE:Y28` → `AGE:5Y25` → `AGE:10Y20` |
| LOC | 도시 → 국가 → ANY | `LOC:CH:zurich` → `LOC:C:CH` |
| POB | 출생 도시 → 출생 국가 → ANY | `POB:DE:berlin` → `POB:C:DE` |
| OCCP | ISCO-08 Unit → Minor → Sub-major → Major → ANY | `OCCP:2512` → `OCCP:251` → `OCCP:25` → `OCCP:2` |
| EDU | 박사·석사·학사 → Tertiary → ANY, 고졸 → ANY, 대학 재학 → ANY | `EDU:MASTER` → `EDU:TERTIARY` |
| INC | low / middle / high / very_high → ANY | `INC:HIGH` |
| REL | 6개 범주 → ANY | `REL:ENGAGED` |
| SEX | male / female → ANY | `SEX:FEMALE` |

- 5년 구간과 10년 구간은 같은 기준점으로 포갭니다 (25–29 ⊂ 20–29).
- 국가는 ISO 3166-1 두 글자 코드로 구분합니다. 기준 집단에 섞여 있던 "United States"/"USA", "United Arab Emirates"/"UAE"가 한 node로 모입니다.
- 주(州)·구성국 단계는 두지 않습니다. "Cleveland, Ohio", "London, England"처럼 국가 자리에 주 이름이 오면 국가로만 연결합니다. "Georgia"처럼 국가이기도 한 이름은 도시가 어느 쪽에 있는지로 가리고, 가릴 수 없으면 PENDING입니다.
- 기준 집단의 도시 표기 갈림 3건("Bejiing"/"Beijing", "Göteborg"/"Gothenburg", "Guaquil")은 `tables.py`의 `CITY_ALIASES`에서 한 표기로 모읍니다.
- ANY는 계산용 뿌리입니다. 억제된 QI를 ANY로 State에 넣지 않습니다.

## 구성

| 파일 | 하는 일 |
| --- | --- |
| `model.py` | node와 부모 관계, 조상·자손 조회, 구조 검사, 내용 해시 |
| `tables.py` | 국가 표(76개), 주·구성국 → 국가 표, 도시 표기 갈림, EDU·INC·REL·SEX node |
| `isco.py`, `data/occp_alias_v0.1.csv` | ISCO-08 그룹 이름, 직업 표기 206종 → ISCO 코드 |
| `builder.py` | 위 표와 기준 집단의 도시 목록으로 온톨로지 생성 (버전 `d1-core-0.1`) |
| `parsing.py` | 한국어·영어 표현 → node. 기준 집단 매핑과 실행 중 정규화가 같은 규칙을 씀 |
| `mapper.py` | 기준 집단 profile → node, 매핑 보고 |
| `normalizer.py` | 추출 관찰 → MAPPED / PENDING / UNMAPPED |
| `index.py` | node별 인원 집합을 미리 계산해 k를 셈 (30만 명 색인 생성 약 3초, 한 번 세는 데 수십 마이크로초) |
| `defender.py` | 공개 전 일반화 탐색 (D1 Stage 11) |

## D1 운영 결정 대비 구현 상태

| D1 결정 | 상태 |
| --- | --- |
| 정규화와 일반화를 코드상 분리 | 구현 (`normalizer.py`는 정규화만, 일반화는 `defender.py`) |
| 모호하면 추측하지 않고 PENDING | 구현 ("20~30대", 도시 이름이 여러 나라에 있는 경우 등) |
| mapping status + reason 기록 | 구현 |
| ANY와 suppression 구분 | 구현 |
| 기준 집단 300명 100% 정규화 | 구현. `python -m ontology`로 확인 (현재 8개 QI 모두 300/300) |
| mapping table hash로 freeze | 구현. 온톨로지 해시와 표별 해시를 `run_config.json`에 기록 |
| determinism, idempotence, k(parent) ≥ k(child) 자동 테스트 | 구현 (`tests/ontology/`) |
| level별 candidate distribution 사전 계산 | 구현. `python -m ontology` 출력 |
| node_id / parent_id / ontology_version 저장 | 구현 |
| 국가 UN M49/ISO | ISO 3166-1 코드 사용 |
| 도시 표기 GeoNames | **미적용.** 기준 집단에 나온 도시만 node로 둠. 그 밖의 도시는 국가 node로 연결 |
| LLM이 허용 node 중 후보 제안 | **미적용.** 규칙과 표로만 해석. 규칙에 없는 표현은 UNMAPPED |
| ISCO-08 table/version 저장 | 부분. 대응표가 쓰는 160개 코드만 담음 (전체 436개 Unit 아님) |

## Defender (D1 Stage 11)

`--backend ontology`에서는 Defender도 D1 방식으로 동작합니다.

1. 이번 응답이 새로 드러내는 QI만 다룹니다. 이미 공개된 값과 같거나 더 넓은 값은 새 정보가 아닙니다.
2. 하한은 `k_floor = min(k_req, 지금 k)`입니다. 그대로 공개해도 하한 이상이고 0명이 되지 않으면 손대지 않습니다.
3. 아니면 QI 하나를 부모로 한 단계 올린 조합부터 차례로 넓혀 가며 찾고, 안전한 조합이 처음 나온 층에서 멈춥니다.
4. 같은 층에 여럿이면 정보 손실(올린 단계 수 ÷ 억제까지의 단계 수의 합)이 가장 작은 것을 고릅니다.
5. 더 올릴 곳이 없으면 그 QI를 억제합니다.

같은 입력에 대한 두 백엔드의 차이 예: 취리히 거주(3명)가 처음 나왔을 때 `risk_defender` 수정본은 바로 억제하고, 온톨로지 백엔드는 스위스(5명)로 일반화합니다.

## 사람 검토가 필요한 항목

아래는 구현하면서 임시로 정한 것입니다. 계층 명세 담당(배성윤)의 확인이 필요합니다.

| 항목 | 지금 정한 방식 | 확인할 점 |
| --- | --- | --- |
| 직업 → ISCO 코드 | 206행 기계 초안. H 131행, M 50행, L 25행 (Part 8에서 "developer", "coder"처럼 응답에 자주 나오는 넓은 표현 33행 추가) | M·L 75행의 코드. `python -m ontology --show-review`로 목록 확인 |
| 구분이 안 되는 직업 | 뒷받침되는 상위 코드에서 멈춤 (예: teacher → 23, engineer → 21) | 상위 코드로 둔 사람은 하위 코드 조건에 걸리지 않음. 이 처리가 맞는지 |
| 은퇴·분류 불가 | ISCO 밖의 보조 node `OCCP:X` (6명) | D1은 자체 분류를 금지함. 별도 처리 방식이 필요한지 |
| "retired", "part-time" 등 수식어 | 떼고 직업만 대조 | 은퇴 여부를 정보로 볼지 |
| EDU 재학 중 | 학사 과정 → 대학 재학, 석사 과정 → 학사, 박사 과정 → Tertiary | 재학을 어느 단계로 볼지 |
| EDU "Diploma in …", "Law Degree", "MD"·"Doctorate in Medicine", 전문학사 | Tertiary | 단계가 맞는지 |
| 한쪽이 열린 나이 범위 ("60세 이상") | 읽지 않음 (맞는 node 없음) | 별도 node가 필요한지 |
| 부정·비교 표현 ("배우자 없음", "평균 이하") | 읽지 않음 | 범주를 정할 규칙이 필요한지 |
| EDU 대학 재학 node | Tertiary 아래가 아니라 ANY 바로 아래 | 위치 |
| AGE "30대 중반" | 10년 구간 (5년 구간 둘에 걸치므로) | 이 해석이 맞는지 |
| 정보 손실 식, 동점 규칙 | 합, QI 순서상 뒤쪽을 올림 | D1 "연구팀 결정 5" |
| 위험 기준 | k_req = 4 (`--threshold 3`) | D1은 k_req 5. `--threshold 4`로 실행하면 5가 됨 |

## 알려진 한계

**정규화하지 못한 값은 Defender가 보지 못합니다.** 가장 중요한 한계입니다. 응답에 QI가 나왔어도 표현을 node로 풀지 못하면(UNMAPPED·PENDING) k 계산에 들어가지 않고, Defender도 개입하지 않으며, State에도 남지 않습니다. D1의 규칙("runtime unknown은 risk constraint에서 제외")을 따른 결과이지만, POC 보고서 실험 3이 보인 우회 경로와 같은 구조입니다.

- 예: "31세, Zurich, software engineer"는 k = 1로 계산되어 Defender가 개입합니다. 같은 내용을 "취리히", "소프트웨어 엔지니어"라고만 쓰고 영어 표현(`value_en`)이 없으면 k는 그대로입니다.
- 한국어 도시 이름과 직업 이름은 표에 없습니다. 추출기가 함께 주는 영어 표현에 의존합니다.
- 실행 요약 표의 "미해석" 열과 trace의 `unresolved_released`가 이런 관찰 수입니다. 이 수가 0이 아니면 k_run은 실제보다 크게 나온 것입니다.
- 줄이려면 추출기 프롬프트에서 `value_en`을 빠짐없이 받거나, 한국어 지명·직업 표를 추가해야 합니다.

그 밖의 한계

- Rewrite LLM이 일반화 표현을 풀어 쓰면 정규화 규칙이 읽지 못할 수 있습니다. 이때는 Defender가 지시한 값으로 State에 기록합니다 (trace의 `adopted_ids`). 끄려면 `GateConfig(adopt_instructed_value=False)`.
- 기준 집단에 없는 값이 응답에 나오면 k가 0이 됩니다. C1에서는 공개 전에 막고, C0에서는 `zero_match`로 기록됩니다.
- 해석 규칙은 정규식입니다. 독립 검토에서 나온 오분류 사례(부정 표현, 재학 표현, 열린 나이 범위 등)는 고치고 테스트로 묶었지만, 다루지 못한 표현이 더 있을 수 있습니다.

## 명령

```bash
python -m ontology                         # 원본 300명 매핑 점검
python -m ontology --population expanded   # 30만 명 기준
python -m ontology --show-review           # 직업 대응표 검토 대상 행
python -m orchestrator --backend ontology --arms C0 C1 --target pers1 --turns 5
```
