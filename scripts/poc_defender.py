#!/usr/bin/env python3
# 누적 State 기반 방어(C1) vs 방어 없음(C0) vs 비누적(Stateless) PoC
#
# PDF 보고서 PART III §3 "PoC 정합" 검증용
# 원 PoC (2026-09-15): Synthea 582명 + Claude Haiku 4.5 + LLM 추출기, 20 target, 8 seed
# 본 스크립트: SynthPAI 300명(또는 300k 확장) + Ground-truth QI (추출 오류 없는 완벽한 공격자)
#
# 주요 검증 항목:
#   C0 방어 없음  → 위험 사건 多 (원 PoC: 20/20)
#   C1 누적 Defender → 위험 사건 0 (원 PoC: 0/20)
#   Stateless Defender → 일부 위험 사건 잔존 (원 PoC: 13/20)

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from risk_defender.defender import defend_candidate_qis
from risk_defender.loader import load_expanded_population, load_population
from risk_defender.risk import calculate_k
from risk_defender.schema import PrivacyState, QIObservation, update_state

# QI 노출 순서 — PDF Stage 2 (AGE, SEX, LOC, OCCP, EDU, REL, INC, POB 중 5개)
QI_ORDER = ["LOC", "OCCP", "AGE", "EDU", "REL"]

DEFAULT_K_REQ = 5
DEFAULT_N_TARGETS = 20
DEFAULT_SEED = 42


@dataclass
class TurnResult:
    target_id: str
    turn: int
    qi_type: str
    raw_value: str | None
    # C0: 방어 없음
    c0_k: int
    c0_risk_event: bool
    # C1: 누적 State Defender
    c1_action: str
    c1_defended_value: str | None
    c1_k: int
    c1_risk_event: bool
    # Stateless: per-turn 평가, 히스토리 미반영
    sl_action: str
    sl_defended_value: str | None
    sl_k: int
    sl_risk_event: bool


def run_target(
    population: list,
    target,
    *,
    k_req: int,
) -> list[TurnResult]:
    """한 target에 대해 5턴 QI 노출 시뮬레이션."""
    c0_state = PrivacyState(target_id=target.person_id, session_id="c0")
    c1_state = PrivacyState(target_id=target.person_id, session_id="c1")
    sl_state = PrivacyState(target_id=target.person_id, session_id="sl")

    results: list[TurnResult] = []
    for turn, qi_type in enumerate(QI_ORDER, start=1):
        raw_value = target.qis.get(qi_type)
        if raw_value is None:
            continue

        qi_obs = QIObservation(
            qi_type=qi_type,
            value=raw_value,
            source_turn=turn,
            confidence=1.0,
            source="ground_truth_poc",
        )

        # ---- C0: 방어 없음 ----
        c0_state = update_state(c0_state, [qi_obs])
        c0_k = calculate_k(population, c0_state).k
        c0_risk = 0 < c0_k < k_req

        # ---- C1: 누적 State Defender ----
        c1_decision = defend_candidate_qis(
            population, c1_state, [qi_obs], threshold=k_req
        )
        c1_state = c1_decision.final_state
        c1_k = calculate_k(population, c1_state).k
        c1_risk = 0 < c1_k < k_req

        if c1_decision.action == "allow":
            c1_defended = raw_value
        elif c1_decision.generalized_qis:
            c1_defended = c1_decision.generalized_qis[0]["to"]
        else:
            c1_defended = None  # suppress

        # ---- Stateless: 매 턴 빈 state로 평가 ----
        fresh_state = PrivacyState(
            target_id=target.person_id, session_id="sl-fresh"
        )
        sl_decision = defend_candidate_qis(
            population, fresh_state, [qi_obs], threshold=k_req
        )

        if sl_decision.action == "allow":
            sl_state = update_state(sl_state, [qi_obs])
            sl_defended = raw_value
        elif sl_decision.generalized_qis:
            gen_qi = QIObservation(
                sl_decision.generalized_qis[0]["qi_type"],
                sl_decision.generalized_qis[0]["to"],
                source_turn=turn,
                confidence=1.0,
            )
            sl_state = update_state(sl_state, [gen_qi])
            sl_defended = sl_decision.generalized_qis[0]["to"]
        else:
            sl_defended = None  # suppress

        sl_k = calculate_k(population, sl_state).k
        sl_risk = 0 < sl_k < k_req

        results.append(
            TurnResult(
                target_id=target.person_id,
                turn=turn,
                qi_type=qi_type,
                raw_value=str(raw_value) if raw_value is not None else None,
                c0_k=c0_k,
                c0_risk_event=c0_risk,
                c1_action=c1_decision.action,
                c1_defended_value=str(c1_defended) if c1_defended is not None else None,
                c1_k=c1_k,
                c1_risk_event=c1_risk,
                sl_action=sl_decision.action,
                sl_defended_value=str(sl_defended) if sl_defended is not None else None,
                sl_k=sl_k,
                sl_risk_event=sl_risk,
            )
        )
    return results


def print_summary(all_results: list[TurnResult], n_targets: int, k_req: int) -> None:
    targets = sorted({r.target_id for r in all_results})

    c0_risky = sum(
        1 for tid in targets
        if any(r.c0_risk_event for r in all_results if r.target_id == tid)
    )
    c1_risky = sum(
        1 for tid in targets
        if any(r.c1_risk_event for r in all_results if r.target_id == tid)
    )
    sl_risky = sum(
        1 for tid in targets
        if any(r.sl_risk_event for r in all_results if r.target_id == tid)
    )

    print()
    print("=" * 60)
    print(f" PoC 결과 요약  (k_req={k_req}, targets={n_targets})")
    print("=" * 60)
    print(f" {'조건':<25} {'위험 세션 수':>10}  {'비율':>8}")
    print(f" {'-'*25} {'-'*10}  {'-'*8}")
    print(f" {'C0  방어 없음':<25} {c0_risky:>10} / {len(targets)}  {c0_risky/len(targets)*100:>6.1f}%")
    print(f" {'C1  누적 Defender':<25} {c1_risky:>10} / {len(targets)}  {c1_risky/len(targets)*100:>6.1f}%")
    print(f" {'Stateless Defender':<25} {sl_risky:>10} / {len(targets)}  {sl_risky/len(targets)*100:>6.1f}%")
    print("=" * 60)
    print(f" 원 PoC 기준값 (2026-09-15): C0=20/20  C1=0/20  Stateless=13/20")
    print()

    print(" 세션별 상세 (위험 사건 발생 턴)")
    print(f" {'target':<10} {'C0 위험턴':<20} {'C1 위험턴':<20} {'Stateless 위험턴'}")
    for tid in targets:
        rows = [r for r in all_results if r.target_id == tid]
        c0_turns = [str(r.turn) for r in rows if r.c0_risk_event]
        c1_turns = [str(r.turn) for r in rows if r.c1_risk_event]
        sl_turns = [str(r.turn) for r in rows if r.sl_risk_event]
        print(
            f" {tid:<10} "
            f"{'T'+',T'.join(c0_turns) if c0_turns else '-':<20} "
            f"{'T'+',T'.join(c1_turns) if c1_turns else '-':<20} "
            f"{'T'+',T'.join(sl_turns) if sl_turns else '-'}"
        )
    print()

    # 턴별 k 평균 출력 (C0 vs C1 vs SL)
    print(" 턴별 평균 k")
    print(f" {'Turn':<6} {'QI':<6} {'C0 avg k':>10} {'C1 avg k':>10} {'SL avg k':>10}")
    for turn_idx, qi_type in enumerate(QI_ORDER, start=1):
        turn_rows = [r for r in all_results if r.turn == turn_idx]
        if not turn_rows:
            continue
        avg_c0 = sum(r.c0_k for r in turn_rows) / len(turn_rows)
        avg_c1 = sum(r.c1_k for r in turn_rows) / len(turn_rows)
        avg_sl = sum(r.sl_k for r in turn_rows) / len(turn_rows)
        print(f" T{turn_idx:<5} {qi_type:<6} {avg_c0:>10.1f} {avg_c1:>10.1f} {avg_sl:>10.1f}")
    print()


def save_csv(all_results: list[TurnResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "target_id", "turn", "qi_type", "raw_value",
        "c0_k", "c0_risk_event",
        "c1_action", "c1_defended_value", "c1_k", "c1_risk_event",
        "sl_action", "sl_defended_value", "sl_k", "sl_risk_event",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_results:
            writer.writerow({
                "target_id": r.target_id,
                "turn": r.turn,
                "qi_type": r.qi_type,
                "raw_value": r.raw_value,
                "c0_k": r.c0_k,
                "c0_risk_event": r.c0_risk_event,
                "c1_action": r.c1_action,
                "c1_defended_value": r.c1_defended_value,
                "c1_k": r.c1_k,
                "c1_risk_event": r.c1_risk_event,
                "sl_action": r.sl_action,
                "sl_defended_value": r.sl_defended_value,
                "sl_k": r.sl_k,
                "sl_risk_event": r.sl_risk_event,
            })


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Runtime Defender PoC — C0/C1/Stateless 비교"
    )
    parser.add_argument("--n-targets", type=int, default=DEFAULT_N_TARGETS)
    parser.add_argument("--k-req", type=int, default=DEFAULT_K_REQ)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--expanded",
        action="store_true",
        help="300k 합성 population 사용 (user_bot_profiles_300000.json 필요)",
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1]
            / "risk_defender"
            / "outputs"
            / "poc_02_cumulative_vs_stateless.csv"
        ),
    )
    args = parser.parse_args()

    base_path = (
        Path(__file__).resolve().parents[1]
        / "risk_defender"
        / "data"
        / "user_bot_profiles_300.json"
    )
    synth_path = base_path.parent / "user_bot_profiles_300000.json"

    if args.expanded:
        population = load_expanded_population(base_path, synth_path=synth_path)
    else:
        population = load_population(base_path)

    if not population:
        raise ValueError("Population이 비어 있습니다.")

    # 재현 가능한 target 선택
    import random
    rng = random.Random(args.seed)
    targets = rng.sample(population, min(args.n_targets, len(population)))

    print(f"Population: {len(population)}명")
    print(f"Targets: {len(targets)}명  (seed={args.seed})")
    print(f"k_req: {args.k_req}  |  QI 순서: {' → '.join(QI_ORDER)}")
    print(f"Population 종류: {'300k 확장' if args.expanded else '원본 300명'}")
    print()

    all_results: list[TurnResult] = []
    for target in targets:
        turn_results = run_target(population, target, k_req=args.k_req)
        all_results.extend(turn_results)

    print_summary(all_results, n_targets=len(targets), k_req=args.k_req)

    out_path = Path(args.out)
    save_csv(all_results, out_path)
    print(f"CSV 저장: {out_path}")


if __name__ == "__main__":
    main()
