# Privacy Risk / Defender Module

이 모듈은 이유진 담당 범위인 Privacy State, 후보군 크기 `k`, 재식별 위험도, projected risk, Defender 일반화/억제 동작을 구현한 코드.

## QI Set

Synthea 합성 환자 기반 의료 시나리오에서 다음 8개 QI를 사용한다. 명칭은 fuzzer 코드와 통일된 영어 대문자 약어를 사용한다.

| 약어 | 의미 | 출처 |
|------|------|------|
| `AGE` | 나이 | `age_node` (사건연결표) |
| `SEX` | 성별 | `sex_node` (사건연결표) |
| `REGION` | 거주/진료 지역 | `region_node` (사건연결표) |
| `DX` | 진단명 (SNOMED CT) | `code_node` (event_type=CONDITION) |
| `PROC` | 시술/처치 (SNOMED CT) | `code_node` (event_type=PROCEDURE) |
| `PERIOD` | 진료 시기 | `period_start_node` (event_type=CONDITION) |
| `MED` | 투약 정보 | `code_node` (event_type=MEDICATION) |
| `PLAN` | 케어플랜 | `code_node` (event_type=CAREPLAN) |

QI 값은 노드 ID 형태로 관리된다. 예: `AGE_Y22`, `SEX_MALE`, `REGION_DATASET_LOCALITY_...`, `DX_SCT_65363002`.

## 데이터 파일

`risk_defender/data/`에 포함되어 있다. 별도 설정 없이 바로 실행 가능하다.

| 파일 | 설명 |
|------|------|
| `risk_defender/data/노드_v0.9.csv` | 69,034개 노드와 계층 구조 (node_id, parent_id, depth, canonical_label) |
| `risk_defender/data/사건연결표_v0.9.csv` | 576명 환자의 51,759개 의료 이벤트 |
| `risk_defender/data/user_patient_profiles_576.json` | 원본 Synthea 576명 환자 프로필 |
| `risk_defender/data/user_patient_profiles_1000.json` | 원본 576명 + 합성 424명, 총 1000명 프로필 **(기본값)** |

### Population 확장 (576 → 1000)

원본 576개만으로는 REGION 하나만 물어봐도 k가 낮아 defender가 과도하게 발동하는 문제가 있었다. 이를 완화하기 위해 합성 데이터 424개를 추가해 총 1000명으로 확장했다.

**합성 방식**: `scripts/generate_synth_population.py`
- `AGE`, `SEX`, `REGION`, `DX`, `PROC`, `MED`: 원본 576개의 빈도 비율로 가중치 샘플링 (분포 유지)
- `PERIOD`: 원본 날짜 범위(1923-02-26 ~ 2026-08-13) 내 랜덤 생성 (원본이 거의 unique라 재샘플링 시 중복 문제 발생)
- seed=42 고정으로 재현 가능

```bash
# 재생성하려면
python3 -m risk_defender.scripts.generate_synth_population --n 1000 --seed 42
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

## QI별 일반화 규칙

일반화는 노드 계층(노드_v0.9.csv)을 따라 parent_id 방향으로 올라간다.

| QI | 일반화 예시 |
|----|------------|
| `AGE` | `AGE_Y22` → `AGE5_20` (20~24세) → `AGE10_20` (20대) → `ANY_AGE` |
| `SEX` | `SEX_MALE` / `SEX_FEMALE` → `ANY_SEX` |
| `REGION` | `REGION_DATASET_LOCALITY_*` (도시) → `REGION_DATASET_STATE_*` (주) → `ANY_REGION` |
| `DX` | `DX_SCT_65363002` → 상위 개념 → `ANY_DX` |
| `PROC` | `PROC_SCT_*` → 상위 개념 → `ANY_PROC` |
| `PERIOD` | `PERIOD_*_DAY_*` (일) → `PERIOD_*_MONTH_*` (월) → `PERIOD_*_YEAR_*` (연) → `ANY_DATE` |
| `MED` | `MED_RX_*` → 상위 분류 → `ANY_MED` |
| `PLAN` | `PLAN_SCT_*` → 상위 분류 → `ANY_PLAN` |

## 실행 예시

```bash
# 프로젝트 루트에서
python3 -m risk_defender.demo --generalize-threshold 5 --suppress-threshold 3

# risk_defender 폴더 안에서
python3 demo.py --generalize-threshold 5 --suppress-threshold 3

# 특정 환자 지정 (UUID 또는 1-based 인덱스)
python3 -m risk_defender.demo --target-id ae3336a0-1686-b8a7-bc13-ccc2967a3f73
python3 -m risk_defender.demo --target-id 2

# 데이터 경로 직접 지정
python3 -m risk_defender.demo \
  --profiles /path/to/user_patient_profiles_1000.json \
  --node-data /path/to/노드_v0.9.csv
```

실행 결과는 `outputs/demo_single_target.csv`에 저장된다. 각 turn마다 원본 QI를 그대로 넣었을 때의 `k_after_raw`, Defender 적용 후의 `k_after_defense`, 일반화 또는 억제 결과가 기록된다.

## Demo 동작 방식

`demo.py`는 재현 가능한 검증을 위해 QI 입력 순서를 고정해서 실행한다.

```python
DEMO_QI_ORDER = ["AGE", "SEX", "REGION", "DX", "PROC", "PERIOD", "MED", "PLAN"]
```

`--target-id`를 지정하지 않으면 population에서 첫 번째 환자를 기본 target으로 사용한다. 대상 환자에 특정 QI(예: PLAN)가 없으면 해당 turn은 건너뛴다.

## 지금까지 한 것

- Privacy State / k / Defender 핵심 모듈 구현 완료
- Synthea 합성 환자 576명을 reference population으로 로딩
- 노드 계층(노드_v0.9.csv)을 활용한 계층적 QI 매칭 및 일반화 구현
- 8개 의료 QI(AGE/SEX/REGION/DX/PROC/PERIOD/MED/PLAN)가 누적될 때 k와 risk gain 계산
- threshold 이하 QI는 노드 계층을 따라 일반화하거나 억제
- 합성 데이터 424개 생성해 reference population을 576 → 1000명으로 확장

## 다음에 할 것



## 핵심 인터페이스

```python
from risk_defender.loader import load_population
from risk_defender.schema import PrivacyState, QIObservation
from risk_defender.risk import evaluate_projected_risk
from risk_defender.defender import defend_candidate_qis

population = load_population()  # 기본값: risk_defender/data/ 내 CSV 사용

state = PrivacyState(target_id="<patient-uuid>", session_id="s1")

projected = evaluate_projected_risk(
    population,
    state,
    [QIObservation("REGION", "REGION_DATASET_STATE_MASSACHUSETTS")],
    threshold=5,
)

decision = defend_candidate_qis(
    population,
    state,
    [QIObservation("REGION", "REGION_DATASET_LOCALITY_E0E1225FF2456D89")],
    generalize_threshold=5,
    suppress_threshold=3,
)
```