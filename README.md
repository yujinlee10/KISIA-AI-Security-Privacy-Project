# ZEROP_PII

멀티턴 대화에서 준식별정보(QI)가 쌓여 대상이 몇 명까지 좁혀지는지(k)를 턴마다 계산하고, 기준 아래로 내려가려는 응답을 공개 전에 일반화·억제하는 Privacy Framework의 POC 구현입니다.

공격 질문 생성(Fuzzer), 대상 시스템(Target LLM), QI 추출, 위험 판정·방어(Risk/Defender), 평가 로그를 한 번의 실행으로 연결합니다.

## 현재 상태

| 항목 | 상태 |
| --- | --- |
| 모듈 구현 및 연결 (질문 생성 → 응답 → 추출 → 판정 → 공개 → State 반영) | 완료 |
| 단위·통합 테스트 | 692개 통과 (LLM 호출은 가짜 client 사용) |
| 실행 갈래 | C0(Defender 없음), C1(공개 전 Defender), C1S(무상태 Defender 대조군) |
| 위험 계산 백엔드 | `risk_defender`(이유진 파트, 기본) / `ontology`(D1 Stage 3 계층 + D1 Stage 11 Defender) |
| Privacy Coverage 기록 (D1 Stage 9) | 턴별 판정과 세션 요약을 trace에 기록 |
| 위험 경로 보관 (D1 Stage 10) | 실행 중 k가 k_req 아래로 내려간 세션의 질문 순서를 `high_risk_paths.jsonl`에 기록 |
| 정답(GT) 대조 평가 `k_val` | `python -m validation` (실행이 끝난 뒤 따로 돌림. 실행 중 코드는 정답을 읽지 않음) |
| Exact Replay (D1 Stage 13) | `python -m orchestrator.replay` (보관된 질문을 새 세션에서 그대로 재실행) |
| 실행 집계 | `python -m orchestrator.report` |
| C2·C3 (경량 언러닝 규칙 생성·주입) | 미구현 |
| 변형 재공격 (D1 Stage 14), Interesting 질문의 Seed 승격 | 미구현 |

모듈별 상세 설명(Risk/Defender 정책, Threshold 선정 근거, Evaluation 로그 컬럼)은 [docs/modules.md](docs/modules.md)에, 온톨로지 계층과 검토가 필요한 항목은 [docs/ontology.md](docs/ontology.md)에 있습니다.

## 폴더 구조

| 폴더 | 담당 | 하는 일 |
| --- | --- | --- |
| `core/` | 양승준 | `.env` 설정 적재, LLM 호출(Anthropic `/v1/messages` 형식), QI 공통 정의 |
| `target_llm/` | 양승준 | SynthPAI 코멘트 적재, persona 단위 검색, Target 응답 생성 |
| `qi_extractor/` | 양승준 | 응답에서 8개 QI 관찰값 추출, 근거 문구 검증 |
| `normalizer/` | 양승준 | 관찰값을 기준 집단 표기로 정규화 (MAPPED / PENDING / UNMAPPED) |
| `attack/` | 양승준 | Seed 레지스트리 적재, 조건 평가, Seed 선택 |
| `ontology/` | 양승준 (계층 명세 검토: 배성윤) | 8개 QI의 표준 값과 상하위 관계, 기준 집단 매핑, 후보 수 색인, 일반화 탐색 |
| `adapters/` | 양승준 | `risk_defender`·`fuzzer` 연결 어댑터, 온톨로지 백엔드(`ontology_risk.py`) |
| `orchestrator/` | 양승준 | 턴 루프, 공개 게이트, 응답 재작성, Coverage 기록, 실행 기록, 위험 경로 보관, 재실행, 집계 |
| `validation/` | 양승준 | 실행이 끝난 뒤 State를 정답과 대조해 `k_val` 계산 (실행 중 코드에서 import 금지) |
| `fuzzer/` | 한재이 | k 피드백 → 변형 전략 → Mutation 지시문 |
| `risk_defender/` | 이유진 | Privacy State, k 계산, 일반화·억제 판정 |
| `evaluation_v1/` | 이정민 | 실험 로그 스키마, 지표 계산 |
| `data/seed_registry/` | 한재이 | O1 72개, O2 45개 Seed |
| `scripts/` | 이유진 | 합성 기준 집단 생성, threshold 실험 |
| `tests/` | — | 위 모듈 테스트 |

팀원 모듈(`fuzzer/`, `risk_defender/`, `evaluation_v1/`)은 수정하지 않고 `adapters/`와 `orchestrator/`에서 불러 씁니다.

## 한 턴의 처리 순서

1. **질문 생성** — 공개된 QI와 턴 수만 보고 조건에 맞는 Seed를 고른 뒤 Mutation LLM으로 질문을 만든다. Seed는 위험 계산에 들어가는 QI를 노리는 것부터 고른다.
2. **Target 응답** — 대상 persona의 코멘트를 문맥으로 원 응답(raw candidate)을 만든다.
3. **추출·정규화** — 원 응답에서 QI를 뽑아 기준 집단 표기로 바꾼다.
4. **공개 게이트**
   - C0: 원 응답을 그대로 공개한다.
   - C1: Defender 판정에 따라 응답을 고쳐 쓰고, 고친 응답에서 다시 추출해 k가 하한 이상인지 확인한다. 끝내 만족하지 못하면 거절 문장을 공개한다.
   - C1S: C1과 같되 Defender가 매 턴 빈 State에서 판정한다. 누적 State가 방어에 얼마나 기여하는지 보기 위한 대조군이다.
5. **State 반영** — 공개된 응답에서 나온 관찰만 State에 넣고 k를 갱신한다.
6. **피드백** — 턴 전후 k를 Fuzzer에 돌려주어 다음 변형 전략을 고른다.

원 응답과 정답(GT)은 공격 쪽 모듈과 State로 넘어가지 않습니다. k 값은 전략 선택에만 쓰이고 Mutation LLM 프롬프트에는 들어가지 않습니다.

## 설치

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/ZEROPII/ZEROP_PII.git
cd ZEROP_PII
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 실행 준비

### 1. 환경변수

```bash
cp .env.example .env
```

`.env`를 열어 세 값을 채웁니다. Fuzzer, Target, Extractor, 재작성기가 모두 이 설정 하나를 씁니다.

```
LLM_API_KEY=
LLM_API_BASE=
LLM_MODEL=
```

`.env`는 `.gitignore`에 등록되어 있습니다. 커밋하지 마세요.

### 2. 데이터

```bash
python -m target_llm.download          # SynthPAI 코멘트 (data/synthpai/)
python -m adapters.prepare_reference    # 기준 집단: 원본 300명 + 합성 299,700명
```

두 데이터 모두 저장소에 올라가지 않으므로 처음 한 번 실행해야 합니다. 기준 집단 파일은 약 70MB입니다.

### 3. 연결 확인

```bash
python -m core.llm_client               # 모델 응답이 오는지 확인
python -m pytest -q                     # 692 passed
python -m ontology                      # 기준 집단이 온톨로지로 전부 풀리는지 점검 (LLM 호출 없음)
```

## 실행

### 전체 실행

```bash
python -m orchestrator --target pers1 --arms C0 C1 --turns 10 --experiment-id run_rd
python -m orchestrator --target pers1 --arms C0 C1 C1S --backend ontology --turns 10 --experiment-id run_onto
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--target` | `pers1` | 대상 persona. 여러 개를 띄어 써서 줄 수 있음 |
| `--arms` | `C0 C1` | 실행할 갈래. `C1S`를 넣으면 무상태 Defender 대조군도 돌린다 (`--conditions`도 같은 뜻) |
| `--backend` | `risk_defender` | 위험 계산 방식. `ontology`는 D1 계층으로 8개 QI를 모두 센다 |
| `--turns` | `5` | 세션당 턴 수 |
| `--threshold` | `3` | 반드시 지키는 경계. k가 이 값 이하면 위험 (k_req = 값 + 1). `risk_defender`의 억제 기준 |
| `--generalize-threshold` | `5` | `risk_defender`가 일반화를 시도하기 시작하는 경계. `ontology` 백엔드는 쓰지 않음 |
| `--population` | `expanded` | `expanded`: 30만 명 / `base`: 원본 300명 |
| `--rng-seed` | `42` | Seed 선택 난수 |
| `--defense-rounds` | `2` | 응답 고쳐 쓰기 최대 횟수 |
| `--experiment-id` | `poc_날짜_시각` | 결과 폴더 이름 |
| `--temperature` | `0` | Target 응답의 temperature. 갈래 간 차이가 응답의 우연한 흔들림에서 오지 않게 고정한다. 음수를 주면 서버 기본값 |
| `--all-seeds` | 꺼짐 | 켜면 k에 들어가지 않는 QI를 묻는 Seed도 고른다 (기본은 백엔드가 세는 QI를 노리는 Seed만) |
| `--paired` | 꺼짐 | 대상마다 첫 갈래가 만든 질문 순서를 나머지 갈래에 그대로 던진다. 갈래를 비교할 때는 켜는 것을 권장 |
| `--plain-mutation` | 꺼짐 | 질문 생성의 보정(Target 자료 범위 안내, 변형 결과의 자료 출처 검사, QI 고른 배분)을 끈다. 이전 동작과 비교할 때만 사용 |
| `--extractor-prompt` | `extractor_system_v2` | 추출 프롬프트 버전. v2는 값마다 표준 영어 표기(`value_en`)를 요구한다. 이전 동작은 `extractor_system_v1` |

턴당 LLM 호출은 질문 생성 1회, Target 1회, 추출 1회이고, C1에서 Defender가 개입하면 재작성과 재추출이 1회씩 더 듭니다. 기준 집단 30만 명 적재에 수 초, 메모리 약 500MB가 필요합니다.

### 질문이 Target에 맞도록 하는 장치

10월 6일 실제 실행(pers1, 12턴)에서 질문 36개 중 26개가 구매 기록·검색 이력처럼 Target에 없는 자료를 물었고, 3개는 변형 LLM의 거부 문장이 질문으로 나갔습니다. Target은 대상 사용자의 게시글만 갖고 있습니다. 이를 막기 위해 `adapters/fuzzer_adapter.py`에 세 가지를 넣었습니다. `fuzzer/` 코드와 프롬프트 파일은 고치지 않았습니다.

| 장치 | 하는 일 |
| --- | --- |
| 자료 범위 안내 | Mutation LLM system prompt(`fuzzer/seedinput/seedprompt.txt`) 뒤에 "Target은 사용자의 게시글만 갖고 있다"는 안내를 붙인다 |
| 변형 결과 검사 | 거부·되묻기 문장(`META_OUTPUT`)과 Target에 없는 자료를 전제로 한 질문(`OUT_OF_SCOPE_SOURCE`)을 버리고 다시 요청한다. 끝내 안 되면 Seed 원문을 보낸다 |
| QI 고른 배분 | 새 Seed를 고를 때 지금까지 가장 적게 물은 QI부터 고른다. 한 QI에 턴이 몰리지 않는다 |

자료 출처 검사는 실제 실행에서 나온 표현을 모은 목록이라 빠진 표현이 있을 수 있습니다. 버린 변형의 수와 사유는 `report.md`의 "공격 쪽" 표에 나옵니다.

**갈래 간 같은 질문 (`--paired`).** 기본 실행에서는 갈래마다 Fuzzer가 따로 질문을 만들어서, Defender가 개입하지 않아도 갈래 간 k가 달라집니다. `--paired`를 켜면 첫 갈래(보통 C0)의 질문 순서를 나머지 갈래에 그대로 던지므로 차이가 공개 게이트에서만 생깁니다. 대신 나머지 갈래의 공격자는 자기 갈래의 응답에 반응하지 않습니다. "방어된 응답을 보고 질문을 바꾸는 공격자"의 결과가 필요하면 `--paired` 없이 돌립니다.

### 결과 파일

`runs/<experiment_id>/` 아래에 생깁니다. 이 폴더는 저장소에 올리지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `experiment_log.csv` | `evaluation_v1/log_schema_v1.py` 형식의 턴별 기록 (k 전후, 위반·교차 여부, 일반화·억제 여부) |
| `trace.jsonl` | 턴별 상세 기록 (질문, 원 응답, 추출·정규화, Defender 판정, 재작성, 공개 응답, Coverage 판정). 세션 끝에는 Coverage 요약 |
| `run_config.json` | 실행 설정 (모델, 프롬프트 버전, temperature, 기준 집단, Seed 파일 해시, 위험 기준, 백엔드, 온톨로지 버전·해시) |
| `high_risk_paths.jsonl` | 실행 중 1 ≤ k < k_req에 도달한 세션마다 한 줄 (질문 순서, Seed, 턴별 k). 그런 세션이 없으면 파일도 없음 |
| `validation.jsonl`, `experiment_log_validated.csv`, `validated_paths.jsonl` | `python -m validation`이 만든다 (아래) |
| `report.md` | `python -m orchestrator.report`가 만든다 (아래) |

`experiment_log.csv`의 `defender` 컬럼은 `none` / `risk_defender` / `ontology_d1`이고, 무상태 갈래는 뒤에 `_stateless`가 붙습니다.

### 실행이 끝난 뒤: 검증 → 집계 → 재실행

세 명령 모두 `runs/` 아래 폴더 이름을 받습니다. 검증과 집계는 LLM을 부르지 않습니다.

```bash
python -m validation run_rd run_onto            # 정답 대조: k_val, QI별 판정, 위험 경로의 검증 상태
python -m orchestrator.report run_rd run_onto   # 집계 표를 화면과 runs/<id>/report.md로
python -m orchestrator.replay --source run_rd --arms C0 C1 --repeats 2   # 보관된 질문 재실행 (LLM 호출)
python -m validation run_rd__replay             # 재실행 결과도 정답 대조
```

**k_run과 k_val.** 실행 중 k(`k_run`)는 State에 들어간 값으로 센 후보 수입니다. 응답이 틀린 값을 말했거나 추출이 틀렸으면 그 후보 안에 대상 본인이 없을 수 있고, 극단적으로는 0명이 됩니다. `k_val`은 State의 QI 중 대상의 실제 값과 맞는 것(같거나 더 넓은 값)만 남겨 다시 센 수입니다. 대상 본인은 항상 포함되므로 1 이상이고, `k_run`보다 작아지지 않습니다. 결과로 보고하는 수치는 `k_val`입니다 (D1 Stage 2).

| 검증 결과 파일 | 내용 |
| --- | --- |
| `validation.jsonl` | 턴마다 `k_run`, `k_val`, QI별 판정(CORRECT / LESS_PRECISE / INCORRECT / UNASSESSABLE), 세션마다 요약 |
| `experiment_log_validated.csv` | `experiment_log.csv`에 `k_val` 컬럼을 채운 사본. 원본은 그대로 둔다 |
| `validated_paths.jsonl` | 보관된 경로에 `validation_status`를 채운 사본. `VALIDATED_HIGH_RISK`(맞는 QI만으로도 k_req 아래) / `NOT_VALIDATED`(틀린 값 때문에 위험해 보였던 경로) |

**재실행(Exact Replay).** 보관된 경로의 질문 원문과 순서를 고정하고 빈 State의 새 세션에서 다시 던집니다. 질문을 다시 만들지 않으므로 Mutation LLM은 부르지 않고, Target 응답·추출·Defender 판정은 새로 합니다. 결과는 `runs/<source>__replay/`에 같은 형식으로 남고 CSV의 `replay_type`이 `exact_replay`입니다. 백엔드·위험 기준·기준 집단·Target temperature는 원래 실행의 `run_config.json`에서 가져옵니다. 화면에 나오는 "재발"은 `k_run` 기준이므로, 확정하려면 재실행 폴더에도 `python -m validation`을 돌립니다.

위험 경로가 이어지는 순서는 다음과 같습니다. 굵은 글씨까지가 구현된 부분입니다.

**실행 → 보관(`high_risk_paths.jsonl`) → 정답 대조(`validated_paths.jsonl`) → 재실행** → 규칙 생성·주입(C2·C3) → 변형 재공격

### 두 가지 위험 계산 백엔드

| | `risk_defender` (기본) | `ontology` |
| --- | --- | --- |
| k 계산 QI | AGE, LOC, OCCP, EDU, REL | 8개 전부 |
| 계층 | `risk_defender` 내부 규칙 | `ontology/` (나이 5년·10년 구간, 직업 ISCO-08 4단계 등) |
| Defender | k > 5 허용 / 3 < k ≤ 5 일반화 시도 / k ≤ 3 바로 억제 | 하한 k_floor = min(k_req, 지금 k). 한 단계씩 올려 보고 안 되면 억제 |
| 구현 | 이유진 파트 코드를 그대로 호출 | `adapters/ontology_risk.py` |

한 번의 실행은 백엔드 하나만 씁니다. 두 방식을 비교하려면 `--experiment-id`를 달리해 두 번 돌립니다.

### 모듈별 확인

전체 실행 전에 단계별로 확인할 때 씁니다.

```bash
python -m target_llm --target pers1 "질문"      # Target 응답만
python -m qi_extractor --target pers1 "질문"    # Target → 추출
python -m adapters --target pers1 "질문"        # Target → 추출 → 정규화 → 위험 판정
python -m adapters.check_fuzzer                 # Seed 선택과 두 턴 변형
```

## 주요 설정과 미결정 사항

| 항목 | 현재 값 | 비고 |
| --- | --- | --- |
| 위험 기준 | 억제 k ≤ 3 (k_req 4), 일반화 시도 k ≤ 5 | D1 의사결정 보고서는 k_req 5. 팀 결정 필요 |
| 기준 집단 | 30만 명 | 국가명·관계 상태 표기가 일부 섞여 있음 |
| k 계산 대상 QI | 기본 백엔드 5개 / `ontology` 8개 | 기본 백엔드에서 SEX, INC, POB는 추출·기록만 함 |
| 일반화 계층 | 기본은 `risk_defender` 계층 | D1 Stage 3 계층은 `--backend ontology`로 사용 |
| 직업 → ISCO 대응표 | `ontology/data/occp_alias_v0.1.csv` (206행) | 기계 초안. 74행은 사람 검토 필요 (`python -m ontology --show-review`) |
| 억제 시점 | 백엔드마다 다름 | `risk_defender`는 k ≤ 3이면 일반화 없이 억제, D1은 일반화를 먼저 시도 |
| Target 검색 | persona 코멘트 전체 | 임베딩 top-k 검색은 미적용 |
| Seed | 117개, 개발 모드 | 사람 검토 전이라 본 실험 모드에서는 적재 거부 |
| Seed 대상 QI | 백엔드가 세는 QI 우선 | 기본 백엔드에서 소득·성별·출생지 Seed는 그 안에서 고를 것이 없을 때만 씀. `--all-seeds`로 해제 |
| 모호한 답의 구체화 | 켜짐 | 공개됐지만 표준 값으로 풀지 못한 답("코딩 일" 등)이 있으면 그 QI의 O2(구체화) Seed를 쓸 수 있음 |
| 추출 프롬프트 | `extractor_system_v2` | 실제 모델로 v1과 비교 검증 전. 정규화 실패가 줄었는지는 `report.md`의 미해석 건수로 확인 |
| State ID | State에 반영된 값 기준 | Part 7까지는 공개 기록 기준이었음. 이전 실행의 `state_id`와는 값이 다름 |
| 지역·나이 해석 | 여러 지역을 나열한 표현은 미해석, 권역 이름은 국가까지 | "중서부, 스위스"는 정하지 않고 "미국 중서부"는 미국으로 읽음. 원문 범위가 표준 표기보다 넓은 나이는 10년 구간까지만 인정 |
| 대상 수 | 실행 명령의 기본값은 pers1 한 명 | 파이프라인 확인용. 결과를 주장하려면 `--target`에 여러 명을 주어야 함. 대상 선정 기준은 미정 |
| 검증의 범위 | State에 들어간 QI만 | 정규화하지 못한 공개분은 `k_val`에도 반영되지 않음 |

## 브랜치와 커밋 규칙

- 작업은 `feat/<이름>` 브랜치에서 하고 `system_integration`으로 PR을 올립니다.
- 커밋 메시지: `FEAT#이슈번호: 제목`, `FIX#이슈번호: 제목`, `REFACTOR#이슈번호: 제목`
- 병합은 "Create a merge commit"을 씁니다.
- `.env`, `data/synthpai/`, 기준 집단 데이터, `runs/`는 커밋하지 않습니다.
