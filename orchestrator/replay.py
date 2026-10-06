# Exact Replay (D1 Stage 13): 보관된 위험 경로의 질문을 새 세션에서 그대로 다시 던진다
#
#   python -m orchestrator.replay --source run_rd                  # run_rd의 보관 경로 전부, 갈래 C0 C1
#   python -m orchestrator.replay --source run_rd --arms C1 --repeats 2
#
# 고정하는 것과 새로 만드는 것 (D1 Stage 13 개발 기준)
#   고정 : 질문 원문과 순서 (보관소의 queries). 질문을 다시 만들지 않으므로 Mutation LLM을 부르지 않는다
#   새로 : 세션(빈 State), Target 응답, 추출, Defender 판정. 원래 응답을 다시 넣지 않는다
#   끄는 것 : Seed 선택, 변형, 전략 선택. 응답 때문에 질문이 어색해져도 그대로 실행한다
#
# 결과는 runs/<source>__replay/ 에 원래 실행과 같은 형식으로 남는다 (CSV의 replay_type = exact_replay).
# 재실행 결과는 위험 경로 보관소에 다시 넣지 않는다.
#
# 판정
#   이 파일이 내는 것은 실행 중 추정(k_run) 기준의 재발 여부다. 논문에 쓸 판정은 정답 대조 값(k_val)이어야 하므로
#   재실행 뒤에도 'python -m validation <source>__replay'를 돌린다.
#   한 번의 재실행으로 방어 효과를 확정하지 않는다 (LLM 응답은 매번 달라질 수 있다). --repeats로 여러 번 돌린다.
#
# 백엔드·위험 기준·기준 집단은 원래 실행의 run_config.json에서 읽어 그대로 쓴다.

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from adapters.replay_fuzzer import ReplayFuzzer
from adapters.risk_adapter import PopulationMode, ReferencePopulation, RiskConfig, load_reference_population
from core.config import get_settings
from core.llm_client import LLMClient
from orchestrator.archive import load_paths
from orchestrator.experiment import (
    DEFAULT_OUTPUT_ROOT,
    Backend,
    ExperimentConfig,
    RiskEngine,
    build_risk_engine,
    build_run_logger,
    build_session_parts,
)
from orchestrator.release_gate import ARM_LABELS, Arm, GateConfig
from orchestrator.run_log import CONFIG_FILE_NAME
from orchestrator.session import SessionResult, SessionRunner
from target_llm.corpus import PersonaCorpus
from orchestrator.rewriter import RewriterConfig
from qi_extractor.extractor import ExtractorConfig
from target_llm.responder import TargetConfig
from target_llm.retriever import PersonaScopeRetriever, Retriever

REPLAY_SUFFIX = "__replay"
BACKEND_BY_NAME = {"risk_defender": Backend.RISK_DEFENDER, "ontology_d1": Backend.ONTOLOGY}


@dataclass(frozen=True)
class ReplayOutcome:
    """
    한 번의 재실행 결과와 원래 경로의 비교 (모두 실행 중 추정 k_run 기준).

    runtime_recurred → 재실행에서도 1 <= k < k_req에 도달했는지
    """
    source_path_id: str
    arm: Arm
    repeat: int
    session_key: str
    original_first_violation_turn: int | None
    original_min_k: int | None
    replay_first_violation_turn: int | None
    replay_min_k: int | None         # 보관소의 min_k와 같은 기준: 0명(ZERO_MATCH)을 뺀 턴별 최소 k
    runtime_recurred: bool
    result: SessionResult

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "source_path_id": self.source_path_id,
            "replay_arm": self.arm.label,
            "repeat": self.repeat,
            "original_first_violation_turn": self.original_first_violation_turn,
            "original_min_k": self.original_min_k,
            "replay_first_violation_turn": self.replay_first_violation_turn,
            "replay_min_k": self.replay_min_k,
            "runtime_recurred": self.runtime_recurred,
        }


def replay_paths(
    paths: Sequence[dict[str, Any]],
    config: ExperimentConfig,
    *,
    client: Any,
    population: ReferencePopulation,
    retriever: Retriever,
    engine: RiskEngine | None = None,
    repeats: int = 1,
    source_config: dict[str, Any] | None = None,
) -> list[ReplayOutcome]:
    """
    input : paths (보관된 경로 기록), config (experiment_id는 재실행 결과 폴더, arms는 재실행할 갈래),
            client, population, retriever, engine, repeats (경로·갈래마다 반복 횟수),
            source_config (원래 실행의 run_config. 재실행 run_config에 함께 남긴다)
    output : ReplayOutcome 목록
    """
    if repeats < 1:
        raise ValueError("repeats는 1 이상이어야 합니다.")
    engine = engine or build_risk_engine(config.backend, population, config.risk)
    parts = build_session_parts(config, client, retriever, engine)
    logger = build_run_logger(config, client, population, replay_type="exact_replay", archive_high_risk_paths=False)
    logger.write_config({
        "experiment_id": config.experiment_id,
        "replay_of": sorted({path["experiment_id"] for path in paths}),
        "source_path_ids": [path["path_id"] for path in paths],
        "arms": [arm.label for arm in config.arms],
        "repeats": repeats,
        "backend": engine.description,
        "risk": {"risk_threshold": config.risk.risk_threshold,
                 "generalize_threshold": config.risk.generalize_threshold, "k_req": config.risk.k_req,
                 "tau_commit": config.risk.tau_commit},
        "gate": {"max_defense_rounds": config.gate.max_defense_rounds, "refusal_text": config.gate.refusal_text,
                 "adopt_instructed_value": config.gate.adopt_instructed_value},
        "population": {"mode": population.mode.value, "size": population.size},
        "prompts": {"target": config.target.prompt_version, "extractor": config.extractor.prompt_version,
                    "rewriter": config.rewriter.prompt_version},
        "temperature": {"target": config.target.temperature, "extractor": config.extractor.temperature,
                        "rewriter": config.rewriter.temperature},
        "model": getattr(client, "model", None),
        "source_run_config": source_config,
    })

    k_req = config.risk.k_req
    outcomes: list[ReplayOutcome] = []
    seen: dict[str, int] = {}
    for path in paths:
        # 같은 experiment_id로 두 번 실행한 보관소에는 같은 path_id가 여러 번 있다. 둘째부터 번호를 붙여 구분한다
        seen[path["path_id"]] = seen.get(path["path_id"], 0) + 1
        source_key = path["path_id"] + (f"#{seen[path['path_id']]}" if seen[path["path_id"]] > 1 else "")
        for arm in config.arms:
            for repeat in range(1, repeats + 1):
                session_key = f"{source_key}|{arm.label}|r{repeat}"
                extra = {"source_path_id": path["path_id"], "repeat": repeat}
                runner = SessionRunner(
                    parts,
                    engine.new_session(path["target_id"], f"{config.experiment_id}-{session_key}"),
                    ReplayFuzzer(path["queries"], config.risk.risk_threshold),
                    arm,
                )
                result = runner.run(
                    path["target_id"], max_turns=len(path["queries"]),
                    on_turn=lambda record, arm=arm, key=session_key, extra=extra, target=path["target_id"]:
                        logger.log_turn(target, arm, record, session_key=key, extra=extra),
                )
                outcome = ReplayOutcome(
                    source_path_id=path["path_id"], arm=arm, repeat=repeat, session_key=session_key,
                    original_first_violation_turn=path.get("first_violation_turn"),
                    original_min_k=path.get("min_k"),
                    replay_first_violation_turn=result.first_violation_turn(k_req),
                    replay_min_k=min((t.commit.k_after for t in result.turns if t.commit.k_after > 0), default=None),
                    runtime_recurred=result.first_violation_turn(k_req) is not None,
                    result=result,
                )
                logger.log_session_end(result, session_key=session_key, extra=outcome.to_log_dict())
                outcomes.append(outcome)
    return outcomes


def load_source_config(run_dir: Path) -> dict[str, Any]:
    path = run_dir / CONFIG_FILE_NAME
    if not path.exists():
        raise FileNotFoundError(f"원래 실행의 설정 파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def config_from_source(source: dict[str, Any], *, experiment_id: str, arms: Sequence[Arm],
                       output_root: Path, max_defense_rounds: int | None = None) -> ExperimentConfig:
    """
    원래 실행의 run_config에서 재실행 설정을 만든다. 질문을 만드는 쪽(Fuzzer)만 빼고 나머지는 원래 실행과 같아야
    하므로 백엔드, 위험 기준, 게이트 설정, 세 prompt 버전, temperature를 모두 가져온다.
    run_config에 없는 항목은 지금 코드의 기본값을 쓴다.
    """
    backend_name = (source.get("backend") or {}).get("name", "risk_defender")
    if backend_name not in BACKEND_BY_NAME:
        raise ValueError(f"알 수 없는 백엔드: {backend_name}")
    risk = source.get("risk") or {}
    gate = source.get("gate") or {}
    prompts = source.get("prompts") or {}
    temperature = source.get("temperature") or {}
    default_gate = GateConfig()
    return ExperimentConfig(
        experiment_id=experiment_id,
        target_ids=tuple(source.get("target_ids") or ("replay",)),
        arms=tuple(arms),
        backend=BACKEND_BY_NAME[backend_name],
        risk=RiskConfig(risk_threshold=risk.get("risk_threshold", RiskConfig().risk_threshold),
                        generalize_threshold=max(risk.get("generalize_threshold", RiskConfig().generalize_threshold),
                                                 risk.get("risk_threshold", RiskConfig().risk_threshold)),
                        tau_commit=risk.get("tau_commit")),
        gate=GateConfig(
            max_defense_rounds=max_defense_rounds or gate.get("max_defense_rounds", default_gate.max_defense_rounds),
            refusal_text=gate.get("refusal_text", default_gate.refusal_text),
            adopt_instructed_value=gate.get("adopt_instructed_value", default_gate.adopt_instructed_value)),
        target=TargetConfig(prompt_version=prompts.get("target", TargetConfig().prompt_version),
                            temperature=temperature.get("target")),
        extractor=ExtractorConfig(prompt_version=prompts.get("extractor", ExtractorConfig().prompt_version),
                                  temperature=temperature.get("extractor", ExtractorConfig().temperature)),
        rewriter=RewriterConfig(prompt_version=prompts.get("rewriter", RewriterConfig().prompt_version),
                                temperature=temperature.get("rewriter", RewriterConfig().temperature)),
        output_root=output_root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="보관된 위험 경로의 질문을 새 세션에서 그대로 재실행 (Exact Replay)")
    parser.add_argument("--source", required=True, help="원래 실행의 experiment_id (runs/ 아래 폴더 이름)")
    parser.add_argument("--arms", nargs="+", default=["C0", "C1"], choices=ARM_LABELS, type=str.upper,
                        help="재실행할 갈래")
    parser.add_argument("--path-id", nargs="*", default=None, help="일부 경로만 재실행 (기본: 전부)")
    parser.add_argument("--repeats", type=int, default=1, help="경로·갈래마다 반복 횟수")
    parser.add_argument("--experiment-id", default=None, help="결과 폴더 이름 (기본: <source>__replay)")
    args = parser.parse_args()

    source_dir = DEFAULT_OUTPUT_ROOT / args.source
    source = load_source_config(source_dir)
    paths = load_paths(source_dir)
    if args.path_id:
        paths = [path for path in paths if path["path_id"] in set(args.path_id)]
    if not paths:
        print(f"재실행할 위험 경로가 없습니다 ({source_dir}). 실행 중 k가 요구 익명성 아래로 내려간 세션이 없었습니다.")
        return

    config = config_from_source(source, experiment_id=args.experiment_id or args.source + REPLAY_SUFFIX,
                                arms=[Arm.parse(label) for label in args.arms], output_root=DEFAULT_OUTPUT_ROOT)
    calls = sum(len(path["queries"]) for path in paths) * len(config.arms) * args.repeats
    print(f"------ 경로 {len(paths)}건 × 갈래 {len(config.arms)}개 × 반복 {args.repeats}회 "
          f"/ Target 호출 {calls}회 + 추출 {calls}회 (C1 개입 시 추가) ------")

    settings = get_settings()
    started = time.perf_counter()
    population = load_reference_population(PopulationMode((source.get("population") or {}).get("mode", "expanded")))
    engine = build_risk_engine(config.backend, population, config.risk)
    print(f"------ 기준 집단 {population.size:,}명 / 백엔드 {engine.description['name']} "
          f"/ k_req {config.risk.k_req} / 준비 {time.perf_counter() - started:.1f}s ------")

    with LLMClient(settings) as client:
        outcomes = replay_paths(paths, config, client=client, population=population,
                                retriever=PersonaScopeRetriever(PersonaCorpus.load()), engine=engine,
                                repeats=args.repeats, source_config=source)

    print(f"\n{'원래 경로':<34}{'갈래':<5}{'반복':>3}  {'원래 첫 위반':>8}  {'원래 최소 k':>9}  "
          f"{'재실행 첫 위반':>9}  {'재실행 최소 k':>10}  재발(실행 중 추정)")
    for outcome in outcomes:
        print(f"{outcome.source_path_id:<34}{outcome.arm.label:<5}{outcome.repeat:>3}  "
              f"{str(outcome.original_first_violation_turn or '-'):>8}  {str(outcome.original_min_k or '-'):>9}  "
              f"{str(outcome.replay_first_violation_turn or '-'):>9}  {str(outcome.replay_min_k or '-'):>10}  "
              f"{'예' if outcome.runtime_recurred else '아니오'}")
    print(f"결과 폴더: {config.run_dir}")
    print(f"다음 단계: python -m validation {config.experiment_id}    # 정답 대조로 재발 여부 확정")


if __name__ == "__main__":
    main()
