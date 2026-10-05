# Privacy Risk / Defender Module

이 모듈은 이유진 담당 범위인 Privacy State, 후보군 크기 `k`, 재식별 위험도, projected risk, Defender 일반화/억제 동작을 구현한 코드

양승준 :O）


---
###pip install이 필요한 module
dotenv  -   fuzzer, - .env 파일을 읽어 오기 위해
request -   fuzzer, - HTTP 요청을 보내기 위해
## QI Set

SynthPAI profile에서 다음 5개 QI를 사용한다. 명칭은 fuzzer 코드와 통일된 영어 대문자 약어를 사용한다.

| 약어 | 의미 | 출처 |
|------|------|------|
| `AGE` | 나이 (decade band) | `profile.age` → `20s`, `30s` 등으로 변환 |
| `LOC` | 거주지 | `profile.city_country` |
| `OCCP` | 직업 | `profile.occupation` |
| `EDU` | 학력 | `profile.education_categorized` 우선, 없으면 `profile.education`에서 파생 |
| `REL` | 관계 상태 | `profile.relationship_status` |

`author`는 내부 식별자인 `person_id`로만 사용한다. `username`은 직접식별자로 보고 QI 계산에는 사용하지 않는다.

## 핵심 인터페이스

```python
from risk_defender.loader import load_population, load_expanded_population
from risk_defender.schema import PrivacyState, QIObservation
from risk_defender.risk import evaluate_projected_risk
from risk_defender.defender import defend_candidate_qis

# 원본 300개
population = load_population("risk_defender/data/user_bot_profiles_300.json")

# 원본 + 합성 300,000개
population = load_expanded_population(
    "risk_defender/data/user_bot_profiles_300.json",
    synth_path="risk_defender/data/user_bot_profiles_300000.json",
)

state = PrivacyState(target_id="pers4", session_id="s1")

projected = evaluate_projected_risk(
    population,
    state,
    [QIObservation("LOC", "Zurich, Switzerland")],
    threshold=5,
)

decision = defend_candidate_qis(
    population,
    state,
    [QIObservation("LOC", "Zurich, Switzerland")],
    generalize_threshold=5,
    suppress_threshold=3,
)
```

## Risk 정의

```text
K_t = 현재 Privacy State에 저장된 QI 조합과 일치하는 reference population 후보군
k_t = |K_t|
Risk_t = 1 / k_t
RiskGain_t = Risk_after - Risk_before
```

`k_after <= threshold`이면 해당 QI 추가가 위험하다고 판단한다. 후보가 0명인 경우(`k = 0`)도 상태가 모순되었거나 지나치게 구체화된 것으로 보고 위험 transition으로 처리한다.

## Defender 정책

두 개의 threshold를 사용한다: `generalize_threshold=5`, `suppress_threshold=3`

| projected k | 동작 |
|-------------|------|
| k > 5 | **allow** — 그대로 허용 |
| 3 < k ≤ 5 | **generalize** — 일반화 시도, 실패 시 suppress |
| k ≤ 3 | **suppress** — 일반화 없이 바로 억제 |

k ≤ suppress_threshold이면 어떤 일반화도 시도하지 않고 즉시 해당 QI를 제거한다. k가 위험 구간(3 < k ≤ 5)일 때만 일반화를 거친다.

QI별 일반화 규칙:

| QI | 일반화 예시 |
|----|------------|
| `LOC` | `Zurich, Switzerland` → `Switzerland` |
| `AGE` | `20s` / `30s` → `adult` / `70s` → `senior` |
| `EDU` | `Master's` / `PhD` → `higher_education` |
| `REL` | `married` / `engaged` → `partnered` / `single` → `not_partnered` |

## 데이터셋 다운로드 방식

기본 실행에서는 SynthPAI GitHub raw URL에서 `user_bot_profiles_300.json`을 자동으로 다운로드한다.

```text
https://raw.githubusercontent.com/eth-sri/SynthPAI/main/data/profiles/user_bot_profiles_300.json
```

다운로드된 파일은 아래 경로에 저장된다.

```text
risk_defender/data/user_bot_profiles_300.json
```

이미 파일이 있으면 다시 다운로드하지 않고 기존 파일을 재사용한다. 새로 받고 싶으면 `--refresh-data` 옵션을 사용한다.

## 실행 예시

VS Code에서 `risk_defender` 폴더 자체를 열어둔 상태라면 터미널에서 다음처럼 실행한다.

```bash
python3 demo.py --generalize-threshold 5 --suppress-threshold 3
```

`risk_defender`의 부모 폴더에서 실행한다면 다음 명령도 사용할 수 있다.

```bash
python3 -m risk_defender.demo --generalize-threshold 5 --suppress-threshold 3
```

실행 결과는 `outputs/demo_single_target.csv`에 저장된다. CSV에는 각 turn마다 원본 QI를 그대로 넣었을 때의 `k_after_raw`, Defender 적용 후의 `k_after_defense`, 일반화 또는 억제 결과가 기록된다.

로컬에 이미 받은 SynthPAI 파일을 직접 지정하고 싶으면 다음처럼 실행한다.

```bash
python3 -m risk_defender.demo \
  --data /path/to/user_bot_profiles_300.json \
  --generalize-threshold 5 --suppress-threshold 3
```

## 확장 Population (300,000개)

원본 300개만으로는 QI 1개만 입력해도 k가 너무 작아 Defender가 과도하게 발동한다. 합성 데이터를 생성해 300,000개로 확장하면 실제 사용 시나리오에 가까운 k 분포를 얻을 수 있다.

**합성 데이터 생성 (최초 1회)**

```bash
# 프로젝트 루트에서 실행
python3 scripts/generate_synth_population.py
```

생성된 파일은 `risk_defender/data/user_bot_profiles_300000.json` (약 70 MB)에 저장된다. seed=42 고정으로 재현 가능하다.

**확장 Population으로 데모 실행**

```bash
# 기본값: 원본 300개
python3 -m risk_defender.demo --generalize-threshold 5 --suppress-threshold 3

# 300,000개 사용 시 --expanded 필요
python3 -m risk_defender.demo --expanded --generalize-threshold 5 --suppress-threshold 3
```

**생성 옵션**

```bash
python3 scripts/generate_synth_population.py --n 300000 --seed 42
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--n` | 300000 | 최종 총 프로필 수 (원본 300개 포함) |
| `--seed` | 42 | 랜덤 시드 (재현 가능성 보장) |
| `--output` | `data/user_bot_profiles_300000.json` | 출력 경로 |
| `--source` | `data/user_bot_profiles_300.json` | 원본 경로 |

## Demo 동작 방식

현재 `demo.py`는 QI를 랜덤으로 선택하지 않는다. 재현 가능한 검증을 위해 기본 target과 QI 입력 순서를 고정해서 실행한다.

`--target-id`를 지정하지 않으면 SynthPAI population에서 첫 번째 persona인 `pers1`을 기본 target으로 사용한다. 이후 해당 target profile에서 5개 QI를 가져와 아래 순서대로 하나씩 Privacy State에 넣어본다.

```python
DEMO_QI_ORDER = ["LOC", "OCCP", "AGE", "EDU", "REL"]
```

따라서 같은 데이터셋, 같은 target, 같은 threshold로 실행하면 CSV 결과도 동일하게 나온다. 다른 target에 대해 확인하고 싶으면 `--target-id`를 지정하면 된다.

```bash
python3 -m risk_defender.demo --expanded --target-id pers2 --generalize-threshold 5 --suppress-threshold 3
```

현재 demo는 랜덤 공격 시뮬레이션이 아니라, 하나의 target에 대해 QI가 순서대로 누적될 때 `k`, risk gain, Defender 동작을 확인하는 재현 가능한 데모이다.

## Threshold 선정 근거

300,000개 population 기준으로 원본 300명 타겟 전체에 대해 threshold 후보 7개를 실험한 결과:

| threshold | allow | generalize | suppress | median k | min k | 평균 허용 QI 수 |
|-----------|-------|------------|----------|----------|-------|----------------|
| 3 | 66.9% | 18.2% | 14.9% | 5 | 4 | 4.26 |
| 5 | 62.7% | 20.8% | 16.5% | 7 | 6 | 4.17 |
| 10 | 56.0% | 23.4% | 20.6% | 14 | 11 | 3.97 |
| 15 | 57.5% | 20.5% | 22.1% | 22 | 16 | 3.90 |
| 20 | 57.7% | 20.1% | 22.1% | 30 | 21 | 3.89 |
| 30 | 60.0% | 16.8% | 23.2% | 45 | 31 | 3.84 |
| 50 | 57.9% | 16.0% | 26.1% | 80 | 51 | 3.69 |

**선택: threshold = 3**

- suppress율이 가장 낮아 정보 손실 최소 (14.9%)
- 평균 4.26개 QI 허용 — 5개 중 거의 다 통과
- min k = 4로 k-anonymity 기준 만족 (k > threshold 항상 보장)
- threshold를 높일수록 suppress가 늘어 오히려 utility 감소

실험 상세 결과: `risk_defender/outputs/poc_01_threshold_sensitivity_summary.csv`

## 실험 결과 파일 (`outputs/`)

팀원들이 직접 열어볼 수 있는 CSV 파일 4개가 있다.

### `demo_single_target.csv`
특정 target 1명에 대해 QI가 턴마다 하나씩 추가될 때 어떤 일이 벌어지는지 보여주는 파일이다. k 값이 어떻게 줄어드는지, Defender가 어느 시점에 일반화·억제를 하는지 한 눈에 볼 수 있다.

```bash
python3 -m risk_defender.demo --expanded --generalize-threshold 5 --suppress-threshold 3  # 기본 target
python3 -m risk_defender.demo --expanded --target-id pers2 --generalize-threshold 5 --suppress-threshold 3  # 다른 target
```

### `poc_01_threshold_sensitivity_summary.csv`
threshold 값을 여러 개 바꿔가며 실험한 **요약** 결과이다. threshold 후보 7개(`3, 5, 10, 15, 20, 30, 50`)에 대해 allow/generalize/suppress 비율과 최종 k 통계가 threshold 하나당 한 줄로 나온다. **threshold=3으로 결정한 근거 파일**이다.

### `poc_01_threshold_sensitivity_detail.csv`
위 summary의 원본 데이터이다. 모든 target × threshold × QI 조합에 대해 각 턴별 raw 로그가 들어 있다. summary 숫자가 이상하다 싶을 때 파고드는 용도이다.

```bash
python3 scripts/threshold_experiment.py --expanded  # 재생성 시
```

### `poc_02_cumulative_vs_stateless.csv`
**C0(방어 없음) / C1(누적 State Defender) / Stateless(기억 없는 방어)** 세 조건을 20명 target에 대해 비교한 파일. 각 턴마다 세 조건의 k 값과 위험 사건 발생 여부가 기록돼 있다.

이 파일이 보여주는 핵심: QI 하나하나는 개별로 봤을 때 안전해 보여도, 쌓이면 위험해지는데 Stateless는 이걸 못 잡습는다. 누적 State가 없으면 방어가 거의 의미 없다는 걸 수치로 증명한 파일이다.

| 조건 | 위험 세션 (300k 기준) | 설명 |
|------|----------------------|------|
| C0 방어 없음 | 20/20 | 아무것도 안 막음 |
| **C1 누적 Defender** | **0/20** | 우리 모듈, 완벽 방어 |
| Stateless Defender | 20/20 | 기억 없는 방어 = 거의 효과 없음 |

```bash
python3 scripts/poc_defender.py --expanded  # 재생성 시
```

## 지금까지 한 것

- State / k / Defender 핵심 모듈 구현 완료. 
- SynthPAI profile 300명(+ 합성 300,000명)을 reference population으로 로딩하고, 5개 QI가 누적될 때 후보군 k와 risk gain을 계산하며, threshold 이하로 떨어지는 QI는 일반화 또는 억제한다. 
- 여러 target에 대한 batch 실험 및 threshold 비교완료 -> 일반화는 threshold5로, suppress는 threshold 3으로 결정

## 다음에 할 것

- QI 몇 개 쓸지 정하기 (5-8개)
# Evaluation (Sprint 1)

log schema + baseline evaluation 파트입니다. Sprint 1 목표는 일단 한 번 끝까지 도는
것이라, 지표를 다 채우기보다 로그를 쌓고 읽고 결과를 뽑는 흐름부터 맞췄습니다.

## 파일

- `log_schema.py` — 로그 한 줄(turn 하나)의 스키마와 CSV로 쌓는 `LogManager`. 직접
  실행하는 파일이 아니라 다른 모듈에서 import해서 사용합니다.
- `eval_baseline.py` — 쌓인 CSV를 읽어서 지표를 계산합니다. 이 파일을 실행합니다.
- `run_demo.py` — 더미 데이터로 확인용

세 파일 다 같은 폴더에 있어야 합니다 (`eval_baseline.py`가 `log_schema.py`를 import).

## 로그 컬럼

한 turn당 한 줄씩 남습니다.

필수: `experiment_id`, `target_id`, `defender`, `turn`, `attack_type`, `mutation_type`,
`qi`, `k_before`, `k_after`, `risk_event`, `generalized`, `suppressed`, `utility_score`

옵션: `transition_id`, `state_id`, `threshold`, `model`, `config`, `replay_type`,
`timestamp`(레코드 생성 시각, 안 넘기면 자동으로 채워짐)

`qi`는 값이 아니라 카테고리 이름만 남깁니다 (`"age|location"` 형태). prompt나 응답
원문도 저장하지 않습니다. 

QI 표기도 걸리는 부분이 있는데, Fuzzer 쪽 target(`AGE`, `LOC`, `SEX`, `INC`...)이
확정 QI 5개(`age`, `location`, `occupation`, `education`, `relationship_status`)와
대소문자도 다르고 항목도 다릅니다. 로그 자체는 어떤 문자열이 와도 그대로 쌓이지만,
표기가 다르면 Coverage 집계가 어긋나므로 통일이 필요합니다.

## 쓰는 법

turn마다 이렇게 기록하면 됩니다.

```python
from log_schema import ExperimentLogRecord, LogManager

logger = LogManager("evaluation/logs/experiment_log.csv")
logger.log_turn(ExperimentLogRecord(
    experiment_id="exp_001", target_id="person_101", defender="none",
    turn=1, attack_type="direct", mutation_type="none",
    qi=["age"], k_before=100, k_after=50,
    risk_event=False, generalized=False, suppressed=False,
))
```

평가는:

```bash
python eval_baseline.py
```

`results/`에 `evaluation_summary.csv/.md`, `coverage_summary.json`이 생성됩니다.
로그 파일이 없거나 비어 있어도 에러 없이 "0건입니다" 안내만 뜨고 끝나므로, 실행
순서를 따로 신경 쓸 필요는 없습니다.

## run_demo.py

실제 로그 없이 파이프라인만 확인하고 싶을 때 사용합니다.

```bash
python run_demo.py
```
