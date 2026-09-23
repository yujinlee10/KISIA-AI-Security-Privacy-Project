# Privacy Risk / Defender Module

이 모듈은 이유진 담당 범위인 Privacy State, 후보군 크기 `k`, 재식별 위험도, projected risk, Defender 일반화/억제 동작을 구현한 코드

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
    threshold=5,
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

1. 후보 QI를 추가해도 projected `k`가 threshold보다 크면 그대로 허용한다.
2. projected `k`가 threshold 이하로 떨어지면 해당 QI의 일반화를 시도한다.
3. 일반화 후 다시 `k`를 계산한다.
4. 일반화해도 안전하지 않으면 해당 QI를 suppress한다.

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
python3 demo.py --threshold 5
```

`risk_defender`의 부모 폴더에서 실행한다면 다음 명령도 사용할 수 있다.

```bash
python3 -m risk_defender.demo --threshold 5
```

실행 결과는 `outputs/risk_defender_demo.csv`에 저장된다. CSV에는 각 turn마다 원본 QI를 그대로 넣었을 때의 `k_after_raw`, Defender 적용 후의 `k_after_defense`, 일반화 또는 억제 결과가 기록된다.

로컬에 이미 받은 SynthPAI 파일을 직접 지정하고 싶으면 다음처럼 실행한다.

```bash
python3 -m risk_defender.demo \
  --data /path/to/user_bot_profiles_300.json \
  --threshold 5
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
python3 -m risk_defender.demo --threshold 5

# 300,000개 사용 시 --expanded 필요
python3 -m risk_defender.demo --expanded --threshold 5
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
python3 -m risk_defender.demo --target-id pers2 --threshold 5
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

실험 상세 결과: `risk_defender/outputs/threshold_experiment_summary.csv`

## 다음에 할 것

State / k / Defender 핵심 모듈 구현 완료. SynthPAI profile 300명(+ 합성 300,000명)을 reference population으로 로딩하고, 5개 QI가 누적될 때 후보군 k와 risk gain을 계산하며, threshold 이하로 떨어지는 QI는 일반화 또는 억제한다.

남은 작업은 여러 target에 대한 batch 실험 및 threshold 비교이다. Fuzzer/LLM 연결은 다른 팀원이 담당한다.
