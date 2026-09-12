# Evaluation (Sprint 1)

log schema + baseline evaluation 파트입니다. Sprint 1 목표는 일단 한 번 끝까지 도는
것이라, 지표를 다 채우기보다 로그를 쌓고 읽고 결과를 뽑는 흐름부터 맞췄습니다.

## 파일

- `log_schema.py` — 로그 한 줄(turn 하나)의 스키마와 CSV로 쌓는 `LogManager`. 직접
  실행하는 파일이 아니라 다른 모듈에서 import해서 씁니다.
- `eval_baseline.py` — 쌓인 CSV를 읽어서 지표를 계산합니다. 이 파일을 실행합니다.
- `run_demo.py` — 더미 데이터로 위 둘이 잘 붙는지 확인하는 용도. 없어도 그만입니다.

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
