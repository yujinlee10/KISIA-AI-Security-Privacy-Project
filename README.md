# ZEROP_PII

멀티턴 대화에서 준식별정보(QI)가 쌓여 대상이 몇 명까지 좁혀지는지(k)를 턴마다 계산하고, 기준 아래로 내려가려는 응답을 공개 전에 일반화·억제하는 Privacy Framework의 POC 구현입니다.

공격 질문 생성(Fuzzer), 대상 시스템(Target LLM), QI 추출, 위험 판정·방어(Risk/Defender), 평가 로그를 한 번의 실행으로 연결합니다.

## 현재 상태

| 항목 | 상태 |
| --- | --- |
| 모듈 구현 및 연결 (질문 생성 → 응답 → 추출 → 판정 → 공개 → State 반영) | 완료 |
| 단위·통합 테스트 | 592개 통과 (LLM 호출은 가짜 client 사용) |
| 실행 갈래 | C0(Defender 없음), C1(공개 전 Defender), C1S(무상태 Defender 대조군) |
| 위험 계산 백엔드 | `risk_defender`(이유진 파트, 기본) / `ontology`(D1 Stage 3 계층 + D1 Stage 11 Defender) |
| Privacy Coverage 기록 (D1 Stage 9) | 턴별 판정과 세션 요약을 trace에 기록 |
| C2·C3 (경량 언러닝 규칙 주입) | 미구현 |
| 정답(GT) 대조 평가 `k_val` | 미구현 (로그 컬럼은 비워 둠) |

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
| `orchestrator/` | 양승준 | 턴 루프, 공개 게이트, 응답 재작성, Coverage 기록, 실행 기록 |
| `fuzzer/` | 한재이 | k 피드백 → 변형 전략 → Mutation 지시문 |
| `risk_defender/` | 이유진 | Privacy State, k 계산, 일반화·억제 판정 |
| `evaluation_v1/` | 이정민 | 실험 로그 스키마, 지표 계산 |
| `data/seed_registry/` | 한재이 | O1 72개, O2 45개 Seed |
| `scripts/` | 이유진 | 합성 기준 집단 생성, threshold 실험 |
| `tests/` | — | 위 모듈 테스트 |

팀원 모듈(`fuzzer/`, `risk_defender/`, `evaluation_v1/`)은 수정하지 않고 `adapters/`와 `orchestrator/`에서 불러 씁니다.

## 한 턴의 처리 순서

1. **질문 생성** — 공개된 QI와 턴 수만 보고 조건에 맞는 Seed를 고른 뒤 Mutation LLM으로 질문을 만든다.
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
python -m pytest -q                     # 592 passed
python -m ontology                      # 기준 집단이 온톨로지로 전부 풀리는지 점검 (LLM 호출 없음)
```

## 실행

### 전체 실행

```bash
python -m orchestrator --target pers1 --arms C0 C1 --turns 5
python -m orchestrator --target pers1 --arms C0 C1 C1S --backend ontology --turns 5
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

턴당 LLM 호출은 질문 생성 1회, Target 1회, 추출 1회이고, C1에서 Defender가 개입하면 재작성과 재추출이 1회씩 더 듭니다. 기준 집단 30만 명 적재에 수 초, 메모리 약 500MB가 필요합니다.

### 결과 파일

`runs/<experiment_id>/` 아래에 생깁니다. 이 폴더는 저장소에 올리지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `experiment_log.csv` | `evaluation_v1/log_schema_v1.py` 형식의 턴별 기록 (k 전후, 위반·교차 여부, 일반화·억제 여부) |
| `trace.jsonl` | 턴별 상세 기록 (질문, 원 응답, 추출·정규화, Defender 판정, 재작성, 공개 응답, Coverage 판정). 세션 끝에는 Coverage 요약 |
| `run_config.json` | 실행 설정 (모델, 프롬프트 버전, 기준 집단, Seed 파일 해시, 위험 기준, 백엔드, 온톨로지 버전·해시) |

`experiment_log.csv`의 `defender` 컬럼은 `none` / `risk_defender` / `ontology_d1`이고, 무상태 갈래는 뒤에 `_stateless`가 붙습니다.

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
| 직업 → ISCO 대응표 | `ontology/data/occp_alias_v0.1.csv` (173행) | 기계 초안. 57행은 사람 검토 필요 (`python -m ontology --show-review`) |
| 억제 시점 | 백엔드마다 다름 | `risk_defender`는 k ≤ 3이면 일반화 없이 억제, D1은 일반화를 먼저 시도 |
| Target 검색 | persona 코멘트 전체 | 임베딩 top-k 검색은 미적용 |
| Seed | 117개, 개발 모드 | 사람 검토 전이라 본 실험 모드에서는 적재 거부 |

## 브랜치와 커밋 규칙

- 작업은 `feat/<이름>` 브랜치에서 하고 `system_integration`으로 PR을 올립니다.
- 커밋 메시지: `FEAT#이슈번호: 제목`, `FIX#이슈번호: 제목`, `REFACTOR#이슈번호: 제목`
- 병합은 "Create a merge commit"을 씁니다.
- `.env`, `data/synthpai/`, 기준 집단 데이터, `runs/`는 커밋하지 않습니다.
