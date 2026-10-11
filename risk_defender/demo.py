# 전체 흐름을 실행해보는 데모 파일
# Synthea 기반 의료 데이터(사건연결표, 노드 테이블)를 사용한다.

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from risk_defender.defender import defend_candidate_qis
    from risk_defender.loader import (
        DEFAULT_NODE_PATH,
        DEFAULT_PROFILES_PATH,
        load_population,
    )
    from risk_defender.risk import calculate_k, evaluate_projected_risk
    from risk_defender.schema import PrivacyState, QIObservation
else:
    from .defender import defend_candidate_qis
    from .loader import DEFAULT_NODE_PATH, DEFAULT_PROFILES_PATH, load_population
    from .risk import calculate_k, evaluate_projected_risk
    from .schema import PrivacyState, QIObservation


DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "outputs" / "demo_single_target.csv"

# QI를 공개하는 순서 (일반적 속성 → 구체적 임상 정보 순)
DEMO_QI_ORDER = [
    "AGE",
    "SEX",
    "REGION",
    "DX",
    "PROC",
    "PERIOD",
    "MED",
    "PLAN",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthea 의료 데이터 기반 Privacy Risk / Defender 데모."
    )
    parser.add_argument(
        "--profiles",
        default=str(DEFAULT_PROFILES_PATH),
        help="환자 프로필 JSON 경로 (기본값: risk_defender/data/user_patient_profiles_576.json)",
    )
    parser.add_argument(
        "--node-data",
        default=str(DEFAULT_NODE_PATH),
        help="노드 테이블 CSV 경로 (기본값: risk_defender/data/노드_v0.9.csv)",
    )
    parser.add_argument(
        "--target-id",
        default=None,
        help="대상 환자 ID (patient UUID 또는 1-based 인덱스). 미지정 시 첫 번째 환자 사용.",
    )
    parser.add_argument(
        "--generalize-threshold",
        type=int,
        default=5,
        help="k ≤ 이 값이면 일반화 시도 (기본값: 5)",
    )
    parser.add_argument(
        "--suppress-threshold",
        type=int,
        default=3,
        help="일반화 후에도 k ≤ 이 값이면 억제 (기본값: 3)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_PATH))
    args = parser.parse_args()

    population = load_population(args.profiles, args.node_data)
    if not population:
        raise ValueError("로드된 환자 레코드가 없습니다.")

    target = _resolve_target(population, args.target_id)
    state = PrivacyState(target_id=target.person_id, session_id="demo-session")

    rows = []
    for turn, qi_type in enumerate(DEMO_QI_ORDER, start=1):
        value = target.qis.get(qi_type)
        if value is None:
            # 해당 QI가 없는 환자는 해당 턴 건너뜀
            continue

        candidate = QIObservation(
            qi_type=qi_type,
            value=value,
            source_turn=turn,
            confidence=1.0,
            source="ground_truth_demo",
        )
        projected = evaluate_projected_risk(
            population,
            state,
            [candidate],
            threshold=args.generalize_threshold,
        )
        decision = defend_candidate_qis(
            population,
            state,
            [candidate],
            generalize_threshold=args.generalize_threshold,
            suppress_threshold=args.suppress_threshold,
        )
        state = decision.final_state
        rows.append(
            {
                "turn": turn,
                "target_id": target.person_id,
                "candidate_qi": f"{candidate.qi_type}={candidate.value}",
                "k_before": projected.k_before,
                "k_after_raw": projected.k_after,
                "risk_gain_raw": round(projected.risk_gain, 6),
                "risky": projected.risky,
                "defense_action": decision.action,
                "generalized_qis": decision.generalized_qis,
                "suppressed_qis": [
                    f"{item.qi_type}={item.value}" for item in decision.suppressed_qis
                ],
                "k_after_defense": decision.final_k,
                "risk_after_defense": round(decision.final_risk, 6),
                "state_after_defense": state.committed_qis(),
            }
        )

    if not rows:
        print("경고: 대상 환자에 QI가 없어 출력할 결과가 없습니다.")
        return

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"로드된 환자 수: {len(population)}")
    print(f"프로필 파일: {args.profiles}")
    print(f"대상 환자: {target.person_id}")
    print(f"최종 Privacy State: {state.committed_qis()}")
    print(f"최종 k: {calculate_k(population, state).k}")
    print(f"결과 저장: {output_path}")


def _resolve_target(population: list, target_id: str | None):
    """target_id가 UUID면 직접 매칭, 숫자면 1-based 인덱스로 해석."""
    if target_id is None:
        return population[0]

    # UUID 직접 매칭
    for record in population:
        if record.person_id == target_id:
            return record

    # 숫자 인덱스 시도 (1-based)
    try:
        idx = int(target_id) - 1
        if 0 <= idx < len(population):
            return population[idx]
    except ValueError:
        pass

    print(f"경고: target_id '{target_id}'을 찾을 수 없어 첫 번째 환자를 사용합니다.")
    return population[0]


if __name__ == "__main__":
    main()
