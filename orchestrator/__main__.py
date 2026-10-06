# 종단 간 실행: python -m orchestrator --target pers1 --arms C0 C1 --turns 5
#   --arms C0 C1 C1S        C1S = 무상태 Defender 대조군
#   --backend ontology      D1 온톨로지 계층 + D1 방식 Defender (기본은 risk_defender)
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

from adapters.fuzzer_adapter import FuzzerConfig
from adapters.risk_adapter import PopulationMode, RiskConfig, load_reference_population
from attack.registry import SeedRegistry
from core.config import get_settings
from core.llm_client import LLMClient
from orchestrator.experiment import (
    Backend,
    ExperimentConfig,
    build_risk_engine,
    default_experiment_id,
    run_experiment,
)
from orchestrator.release_gate import ARM_LABELS, Arm, GateConfig
from orchestrator.session import SessionResult, TurnRecord
from qi_extractor.extractor import DEFAULT_PROMPT_VERSION as DEFAULT_EXTRACTOR_PROMPT
from qi_extractor.extractor import ExtractorConfig
from target_llm.responder import TargetConfig
from target_llm.corpus import PersonaCorpus
from target_llm.retriever import PersonaScopeRetriever

PREVIEW_CHARS = 300


def main() -> None:
    args = _parse_args()
    config = ExperimentConfig(
        experiment_id=args.experiment_id or default_experiment_id(),
        target_ids=tuple(args.target),
        arms=tuple(Arm.parse(name) for name in args.arms),
        backend=Backend(args.backend),
        max_turns=args.turns,
        rng_seed=args.rng_seed,
        risk=RiskConfig(risk_threshold=args.threshold,
                        generalize_threshold=max(args.generalize_threshold, args.threshold)),
        gate=GateConfig(max_defense_rounds=args.defense_rounds),
        restrict_seeds_to_counted_qis=not args.all_seeds,
        target=TargetConfig(temperature=None if args.temperature < 0 else args.temperature),
        extractor=ExtractorConfig(prompt_version=args.extractor_prompt),
        paired_questions=args.paired,
        fuzzer=(FuzzerConfig(target_scope_note=None, out_of_scope_sources=(), balance_qis=False)
                if args.plain_mutation else None),
    )

    if (config.run_dir / "trace.jsonl").exists():
        print(f"주의: {config.run_dir}에 이전 실행 기록이 있습니다. 이번 결과가 그 뒤에 이어 붙습니다. "
              "섞이지 않게 하려면 지금 멈추고(Ctrl-C) --experiment-id를 바꿔 다시 실행하세요.")

    settings = get_settings()
    registry = SeedRegistry.load()
    corpus = PersonaCorpus.load()

    started = time.perf_counter()
    population = load_reference_population(PopulationMode(args.population))
    engine = build_risk_engine(config.backend, population, config.risk)
    print(f"------ 기준 집단 {population.size:,}명 ({population.mode.value}) "
          f"준비 {time.perf_counter() - started:.1f}s / Seed {len(registry)}개 ({registry.mode.value}) ------")
    print(f"------ 백엔드 {engine.description['name']} (k 계산 QI {engine.description['qi_in_k']}개) "
          f"/ k_req {config.risk.k_req} / {engine.description['defender_policy']} ------")
    mapping = engine.description.get("reference_mapping")
    if mapping and not mapping["fully_mapped"]:
        print("주의: 기준 집단에 온톨로지로 풀지 못한 값이 있습니다. 'python -m ontology'로 확인하세요.")

    with LLMClient(settings) as client:
        results = run_experiment(
            config,
            client=client,
            population=population,
            retriever=PersonaScopeRetriever(corpus),
            registry=registry,
            settings_description=settings.describe(),
            engine=engine,
            on_turn=_print_turn,
        )

    _print_summary(results, config)
    if any(result.unresolved_released for result in results):
        print("미해석: 공개됐지만 정규화하지 못해 k에 반영되지 않은 관찰 수입니다. "
              "많으면 k가 실제보다 크게 나옵니다 (trace.jsonl의 released_observations에서 확인).")
    archived = sum(1 for result in results if result.first_violation_turn(config.risk.k_req) is not None)
    print(f"결과 폴더: {config.run_dir}")
    print(f"위험 경로 보관: {archived}건 (실행 중 추정 기준, 검증 전)")
    print(f"다음 단계: python -m validation {config.experiment_id}    # 정답 대조 (LLM 호출 없음)")
    print(f"           python -m orchestrator.report {config.experiment_id}    # 집계 표")
    if archived:
        print(f"           python -m orchestrator.replay --source {config.experiment_id}    # 보관된 질문 재실행 (LLM 호출)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공격 → 응답 → 추출 → 위험 판정 → 공개 → State 반영을 반복 실행")
    parser.add_argument("--target", nargs="+", default=["pers1"], help="대상 persona (여러 개 가능)")
    parser.add_argument("--arms", "--conditions", dest="arms", nargs="+", default=["C0", "C1"],
                        choices=ARM_LABELS, type=str.upper,
                        help="C0: Defender 없음 / C1: 공개 전 Defender / C1S: 무상태 Defender (대조군)")
    parser.add_argument("--backend", choices=[b.value for b in Backend], default=Backend.RISK_DEFENDER.value,
                        help="risk_defender: 이유진 파트 계층·판정 / ontology: D1 온톨로지 계층·판정")
    parser.add_argument("--turns", type=int, default=5, help="세션당 턴 수")
    parser.add_argument("--population", choices=[m.value for m in PopulationMode], default="expanded")
    parser.add_argument("--threshold", "--suppress-threshold", dest="threshold", type=int,
                        default=RiskConfig().risk_threshold,
                        help="반드시 지키는 경계 (k가 이 값 이하면 위험. k_req = 이 값 + 1)")
    parser.add_argument("--generalize-threshold", type=int, default=RiskConfig().generalize_threshold,
                        help="risk_defender가 일반화를 시도하기 시작하는 경계 (ontology 백엔드는 쓰지 않음)")
    parser.add_argument("--rng-seed", type=int, default=42, help="Seed 선택 난수")
    parser.add_argument("--defense-rounds", type=int, default=GateConfig().max_defense_rounds,
                        help="응답 고쳐 쓰기 최대 횟수")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="Target 응답 temperature (기본 0: 갈래 간 응답 차이를 줄인다. 음수를 주면 서버 기본값)")
    parser.add_argument("--all-seeds", action="store_true",
                        help="백엔드가 k에 넣지 않는 QI를 묻는 Seed도 고른다 (기본은 k에 넣는 QI의 Seed만)")
    parser.add_argument("--paired", action="store_true",
                        help="대상마다 첫 갈래가 만든 질문을 나머지 갈래에 그대로 던진다 (갈래 간 질문 차이를 없앤다)")
    parser.add_argument("--plain-mutation", action="store_true",
                        help="질문 생성의 보정을 끈다: Target 자료 범위 안내, 변형 결과의 자료 출처 검사, QI 고른 배분 "
                             "(Part 8까지의 동작과 비교할 때 쓴다)")
    parser.add_argument("--extractor-prompt", default=DEFAULT_EXTRACTOR_PROMPT,
                        help="추출기 prompt 버전 (qi_extractor/prompts/ 안의 파일 이름)")
    parser.add_argument("--experiment-id", default=None, help="결과 폴더 이름 (기본: poc_날짜_시각)")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────
# 화면 출력 (기록은 run_log가 따로 한다)
# ──────────────────────────────────────────────────────────────

def _print_turn(target_id: str, arm: Arm, record: TurnRecord) -> None:
    attack, release, commit = record.attack, record.release, record.commit
    start = f"새 Seed({attack.reset_reason})" if attack.reset_reason else f"변형({attack.strategy})"
    print(f"\n------ [{target_id} {arm.label}] 턴 {record.turn} : {attack.seed_id} "
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
                print(f"  {item.round_no}차 일반화 [{instruction.attribute}] {instruction.raw_value} → {instruction.phrase}")
            else:
                print(f"  {item.round_no}차 억제 [{instruction.attribute}] {instruction.raw_value}")
    print("응답 :", _preview(release.released_response))
    flags = [name for name, on in (("새 QI", record.coverage.new_qi), ("새 State", record.coverage.new_state),
                                   ("Interesting", record.coverage.interesting)) if on]
    print(f"k_run : {commit.k_before:,} → {commit.k_after:,} / 관계 {record.relation} "
          f"/ 피드백 {record.feedback_status} / {', '.join(flags) or '변화 없음'}")
    print(f"공개 상태 : {record.disclosed_after}")


def _print_summary(results: list[SessionResult], config: ExperimentConfig) -> None:
    print("\n====== 요약 ======")
    print(f"{'target':<10}{'갈래':<5}{'턴':>3}  {'처음 k':>9}  {'최소 k':>9}  {'최종 k':>9}  "
          f"{'첫 위반 턴':>8}  {'개입':>4}  {'미해석':>5}  {'QI':>4}  {'쌍':>5}  {'State':>5}  종료")
    for result in results:
        violation = result.first_violation_turn(config.risk.k_req)
        interventions = sum(1 for turn in result.turns if turn.release.intervened)
        coverage = result.coverage
        print(f"{result.target_id:<10}{result.arm.label:<5}{len(result.turns):>3}  "
              f"{result.k_initial:>9,}  {result.k_min:>9,}  {result.k_final:>9,}  "
              f"{(str(violation) if violation else '-'):>8}  {interventions:>4}  {result.unresolved_released:>5}  "
              f"{len(coverage['qi_seen']):>2}/8  {len(coverage['pair_seen']):>2}/28  {coverage['unique_states']:>5}  "
              f"{result.stop_reason.value}" + (f" ({result.stop_detail})" if result.stop_detail else ""))


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS] + " …"


if __name__ == "__main__":
    main()
