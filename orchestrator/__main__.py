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
import json
from pathlib import Path
import time

from adapters.fuzzer_adapter import FuzzerConfig
from adapters.risk_adapter import PopulationMode, RiskConfig, load_reference_population
from attack.registry import SeedRegistry
from core.config import get_settings
from core.llm_client import LLMClient
from orchestrator.archive import should_archive
from orchestrator.experiment import (
    completed_targets,
    Backend,
    ExperimentConfig,
    build_risk_engine,
    default_experiment_id,
    run_experiment,
)
from orchestrator.release_gate import ARM_LABELS, Arm, GateConfig
from orchestrator.rewriter import DEFAULT_PROMPT_VERSION as DEFAULT_REWRITER_PROMPT
from orchestrator.rewriter import RewriterConfig
from orchestrator.session import SessionResult, StopReason, TurnRecord
from qi_extractor.extractor import DEFAULT_PROMPT_VERSION as DEFAULT_EXTRACTOR_PROMPT
from qi_extractor.extractor import ExtractorConfig
from target_llm.responder import TargetConfig
from target_llm.corpus import PersonaCorpus
from target_llm.retriever import PersonaScopeRetriever

PREVIEW_CHARS = 300


def main() -> None:
    args = _parse_args()
    target_ids = load_target_ids(args.targets_file) if args.targets_file else tuple(args.target)
    config = ExperimentConfig(
        experiment_id=args.experiment_id or default_experiment_id(),
        target_ids=target_ids,
        arms=tuple(Arm.parse(name) for name in args.arms),
        backend=Backend(args.backend),
        max_turns=args.turns,
        rng_seed=args.rng_seed,
        risk=RiskConfig(risk_threshold=args.threshold,
                        generalize_threshold=max(args.generalize_threshold, args.threshold),
                        tau_commit=args.min_confidence),
        gate=GateConfig(max_defense_rounds=args.defense_rounds,
                        check_residual_evidence=not args.no_residual_check),
        restrict_seeds_to_counted_qis=not args.all_seeds,
        target=TargetConfig(temperature=None if args.temperature < 0 else args.temperature),
        extractor=ExtractorConfig(prompt_version=args.extractor_prompt),
        rewriter=RewriterConfig(prompt_version=args.rewriter_prompt),
        paired_questions=args.paired,
        resume=args.resume,
        stop_on_violation=args.stop_on_violation,
        fuzzer=(FuzzerConfig(target_scope_note=None, out_of_scope_sources=(), balance_qis=False)
                if args.plain_mutation else None),
    )

    if args.resume:
        done = completed_targets(config.run_dir / "trace.jsonl", [arm.label for arm in config.arms])
        left = [target_id for target_id in config.target_ids if target_id not in done]
        print(f"------ 이어서 실행: 끝난 대상 {len(done)}명은 건너뛰고 {len(left)}명을 돌립니다 "
              f"({', '.join(left) or '남은 대상 없음'}) ------")
        if not left:
            return
    elif (config.run_dir / "trace.jsonl").exists():
        print(f"주의: {config.run_dir}에 이전 실행 기록이 있습니다. 이번 결과가 그 뒤에 이어 붙습니다. "
              "멈춘 실행을 이어 돌리려면 --resume을 붙이고, 새로 돌리려면 --experiment-id를 바꾸세요.")
    print(estimate_calls(len(config.target_ids), len(config.arms), config.max_turns, config.paired_questions))

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
    failed = [result for result in results if result.stop_reason is StopReason.LLM_ERROR]
    if failed:
        print(f"\n중단: {failed[-1].target_id}/{failed[-1].arm.label}에서 LLM 호출이 실패해 남은 대상은 돌리지 않았습니다.\n"
              f"  사유: {(failed[-1].stop_detail or '')[:300]}\n"
              "  API 한도·키·네트워크를 확인한 뒤 같은 명령에 --resume을 붙여 다시 실행하면 끝난 대상은 건너뜁니다.")
    if any(result.unresolved_released for result in results):
        print("미해석: 공개됐지만 정규화하지 못해 k에 반영되지 않은 관찰 수입니다. "
              "많으면 k가 실제보다 크게 나옵니다 (trace.jsonl의 released_observations에서 확인).")
    archived = sum(1 for result in results if should_archive(result, config.risk.k_req))
    print(f"결과 폴더: {config.run_dir}")
    print(f"위험 경로 보관: {archived}건 (실행 중 k가 k_req 아래로 내려갔거나 0이 된 세션. 검증 전)")
    print(f"다음 단계: python -m validation {config.experiment_id}    # 정답 대조 (LLM 호출 없음)")
    print(f"           python -m orchestrator.report {config.experiment_id}    # 집계 표")
    if archived:
        print(f"           python -m orchestrator.replay --source {config.experiment_id}    # 보관된 질문 재실행 (LLM 호출)")


def load_target_ids(path: str) -> tuple[str, ...]:
    """대상 목록 파일에서 ID만 읽는다. 파일의 다른 내용(층, k_reachable)은 실행 중 코드로 넘기지 않는다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    target_ids = data["target_ids"] if isinstance(data, dict) else data
    if not target_ids or not all(isinstance(item, str) for item in target_ids):
        raise ValueError(f"{path}: target_ids가 비어 있거나 문자열 목록이 아닙니다.")
    return tuple(target_ids)


def estimate_calls(targets: int, arms: int, turns: int, paired: bool) -> str:
    """실행 전 LLM 호출 수 어림 (Defender 개입에 따른 재작성·재추출은 별도)."""
    sessions = targets * (1 if paired else arms)
    base = sessions * turns * 3            # 질문 생성 + Target + 추출
    return (f"------ 예상 LLM 호출: 약 {base:,}회 (대상 {targets}명 × "
            f"{'질문·원 응답 공유' if paired else f'갈래 {arms}개'} × {turns}턴 × 3) "
            "+ Defender가 개입한 턴마다 재작성·재추출 ------")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공격 → 응답 → 추출 → 위험 판정 → 공개 → State 반영을 반복 실행")
    parser.add_argument("--target", nargs="+", default=["pers1"], help="대상 persona (여러 개 가능)")
    parser.add_argument("--targets-file", default=None,
                        help="대상 목록 파일 (python -m validation.select_targets가 만든 JSON). 주면 --target 대신 쓴다")
    parser.add_argument("--resume", action="store_true",
                        help="같은 --experiment-id의 이전 기록에서 모든 갈래가 끝난 대상은 건너뛰고 나머지만 돌린다")
    parser.add_argument("--stop-on-violation", action="store_true",
                        help="실행 중 k가 k_req 아래(1 이상)로 내려간 턴에서 그 대상의 질문을 끝낸다 (호출 절약). "
                             "k가 0이 된 경우에는 끝내지 않는다")
    parser.add_argument("--no-residual-check", action="store_true",
                        help="고쳐 쓴 응답에 지운 값의 근거 문구가 남았는지 검사하지 않는다 (Part 10까지의 동작)")
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
    parser.add_argument("--min-confidence", type=int, default=None, choices=[1, 2, 3, 4, 5],
                        help="추출기 확신도가 이 값보다 낮은 관찰은 State에 넣지 않는다 (기본: 제한 없음). "
                             "추측성 값이 State를 오염시키는지 비교할 때 쓴다")
    parser.add_argument("--rewriter-prompt", default=DEFAULT_REWRITER_PROMPT,
                        help="재작성 prompt 버전 (orchestrator/prompts/ 안의 파일 이름)")
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
