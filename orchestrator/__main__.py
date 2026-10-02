# 종단 간 실행: python -m orchestrator --target pers1 --conditions C0 C1 --turns 5
#
# 준비물 (없으면 안내 메시지와 함께 멈춘다)
#   .env                                   : LLM_API_KEY, LLM_API_BASE, LLM_MODEL
#   python -m target_llm.download          : SynthPAI 코멘트
#   python -m adapters.prepare_reference   : 기준 집단 30만 명
#
# LLM 호출 수 (턴당): 질문 생성 1 + Target 1 + 추출 1, C1에서 개입이 있으면 재작성 1 + 재추출 1이 더 든다.
# 결과는 runs/<experiment_id>/ 아래에 남는다 (experiment_log.csv, trace.jsonl, run_config.json).

from __future__ import annotations

import argparse
import time

from adapters.risk_adapter import PopulationMode, RiskConfig, load_reference_population
from attack.registry import SeedRegistry
from core.config import get_settings
from core.llm_client import LLMClient
from orchestrator.experiment import ExperimentConfig, default_experiment_id, run_experiment
from orchestrator.release_gate import SUPPORTED_CONDITIONS, Condition, GateConfig
from orchestrator.session import SessionResult, TurnRecord
from target_llm.corpus import PersonaCorpus
from target_llm.retriever import PersonaScopeRetriever

PREVIEW_CHARS = 300


def main() -> None:
    args = _parse_args()
    config = ExperimentConfig(
        experiment_id=args.experiment_id or default_experiment_id(),
        target_ids=tuple(args.target),
        conditions=tuple(Condition(name) for name in args.conditions),
        max_turns=args.turns,
        rng_seed=args.rng_seed,
        risk=RiskConfig(risk_threshold=args.threshold),
        gate=GateConfig(max_defense_rounds=args.defense_rounds),
    )

    settings = get_settings()
    registry = SeedRegistry.load()
    corpus = PersonaCorpus.load()

    started = time.perf_counter()
    population = load_reference_population(PopulationMode(args.population))
    print(f"------ 기준 집단 {population.size:,}명 ({population.mode.value}) "
          f"적재 {time.perf_counter() - started:.1f}s / Seed {len(registry)}개 ({registry.mode.value}) "
          f"/ 위험 기준 k<={config.risk.risk_threshold} (k_req {config.risk.k_req}) ------")

    with LLMClient(settings) as client:
        results = run_experiment(
            config,
            client=client,
            population=population,
            retriever=PersonaScopeRetriever(corpus),
            registry=registry,
            settings_description=settings.describe(),
            on_turn=_print_turn,
        )

    _print_summary(results, config)
    print(f"결과 폴더: {config.run_dir}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공격 → 응답 → 추출 → 위험 판정 → 공개 → State 반영을 반복 실행")
    parser.add_argument("--target", nargs="+", default=["pers1"], help="대상 persona (여러 개 가능)")
    parser.add_argument("--conditions", nargs="+", default=["C0", "C1"],
                        choices=sorted(c.value for c in SUPPORTED_CONDITIONS),
                        help="C0: Defender 없음 / C1: 공개 전 Defender")
    parser.add_argument("--turns", type=int, default=5, help="세션당 턴 수")
    parser.add_argument("--population", choices=[m.value for m in PopulationMode], default="expanded")
    parser.add_argument("--threshold", type=int, default=RiskConfig().risk_threshold,
                        help="위험 기준 (k가 이 값 이하면 위험)")
    parser.add_argument("--rng-seed", type=int, default=42, help="Seed 선택 난수")
    parser.add_argument("--defense-rounds", type=int, default=GateConfig().max_defense_rounds,
                        help="응답 고쳐 쓰기 최대 횟수")
    parser.add_argument("--experiment-id", default=None, help="결과 폴더 이름 (기본: poc_날짜_시각)")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────
# 화면 출력 (기록은 run_log가 따로 한다)
# ──────────────────────────────────────────────────────────────

def _print_turn(target_id: str, condition: Condition, record: TurnRecord) -> None:
    attack, release, commit = record.attack, record.release, record.commit
    start = f"새 Seed({attack.reset_reason})" if attack.reset_reason else f"변형({attack.strategy})"
    print(f"\n------ [{target_id} {condition.value}] 턴 {record.turn} : {attack.seed_id} "
          f"({attack.objective}/{attack.scenario_code}/{attack.target_qi}) {start} ------")
    print("질문 :", _preview(attack.query))
    if record.raw_extraction_error:
        print("추출 실패 :", record.raw_extraction_error)
    for obs in record.raw_normalized:
        print(f"  raw  [{obs.attribute}] {obs.raw_value} → {obs.status.value}:{obs.canonical_value}")

    line = f"공개 : {release.path.value}"
    if release.refusal_reason:
        line += f" ({release.refusal_reason.value})"
    if release.defender_enabled:
        line += f" / raw 그대로면 k {release.k_raw_projected:,} / 하한 {release.k_floor:,}"
    print(line)
    for item in release.rounds:
        for instruction in (item.rewrite.instructions if item.rewrite else ()):
            if instruction.replacement:
                print(f"  {item.round_no}차 일반화 [{instruction.attribute}] {instruction.raw_value} → {instruction.replacement}")
            else:
                print(f"  {item.round_no}차 억제 [{instruction.attribute}] {instruction.raw_value}")
    print("응답 :", _preview(release.released_response))
    print(f"k_run : {commit.k_before:,} → {commit.k_after:,} / 관계 {record.relation} "
          f"/ 피드백 {record.feedback_status} / 공개 상태 {record.disclosed_after}")


def _print_summary(results: list[SessionResult], config: ExperimentConfig) -> None:
    print("\n====== 요약 ======")
    print(f"{'target':<10}{'조건':<5}{'턴':>3}  {'처음 k':>9}  {'최소 k':>9}  {'최종 k':>9}  "
          f"{'첫 위반 턴':>8}  {'개입':>4}  종료")
    for result in results:
        violation = result.first_violation_turn(config.risk.k_req)
        interventions = sum(1 for turn in result.turns if turn.release.intervened)
        print(f"{result.target_id:<10}{result.condition.value:<5}{len(result.turns):>3}  "
              f"{result.k_initial:>9,}  {result.k_min:>9,}  {result.k_final:>9,}  "
              f"{(str(violation) if violation else '-'):>8}  {interventions:>4}  "
              f"{result.stop_reason.value}" + (f" ({result.stop_detail})" if result.stop_detail else ""))


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS] + " …"


if __name__ == "__main__":
    main()
