# Privacy Risk / Defender Module

이 모듈은 이유진 담당 v0.1 범위인 Privacy State, 후보군 크기 `k`, 재식별 위험도, projected risk, Defender 일반화/억제 동작을 구현한 코드

## v0.1 QI Set

현재 구현은 역할분담 문서 v0.1 기준에 맞춰 SynthPAI profile에서 다음 5개 QI만 사용한다.

1. `age`: `profile.age`에서 추출하며, 정수 나이는 `20s`, `30s` 같은 decade band로 변환한다.
2. `location`: `profile.city_country`에서 추출한다.
3. `occupation`: `profile.occupation`에서 추출한다.
4. `education`: `profile.education_categorized`를 우선 사용하고, 없으면 `profile.education`에서 파생한다.
5. `relationship_status`: `profile.relationship_status`에서 추출한다.

`author`는 내부 식별자인 `person_id`로만 사용한다. `username`은 직접식별자로 보고 QI 계산에는 사용하지 않는다.

## 핵심 인터페이스

```python
population = load_population("data/synthpai.jsonl")
state = PrivacyState(target_id="pers4", session_id="s1")

projected = evaluate_projected_risk(
    population,
    state,
    [QIObservation("location", "Zurich, Switzerland")],
    threshold=5,
)

decision = defend_candidate_qis(
    population,
    state,
    [QIObservation("location", "Zurich, Switzerland")],
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

현재 v0.1 location 일반화 규칙은 다음과 같다.

```text
City, Country -> Country
```

예를 들어 `Zurich, Switzerland`는 `Switzerland` 수준으로 일반화할 수 있다.

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

## 다음에 할 것

v0.1에서 맡은 State / k / Defender 핵심 모듈은 구현했다. SynthPAI profile 300명을 reference population으로 로딩하고, 5개 QI가 누적될 때 후보군 k와 risk gain을 계산하며, threshold 이하로 떨어지는 QI는 일반화 또는 억제하도록 했다.

남은 작업은 Fuzzer 또는 LLM 응답에서 추출된 QI를 이 모듈의 입력 형식으로 연결하고, 여러 target에 대한 batch 실험 및 threshold 비교를 수행하는 것이다.
