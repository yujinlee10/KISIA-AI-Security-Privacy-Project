# 실험 한 번(run)을 조립해 돌린다: target 목록 × 실험 갈래(arm) 목록 → 세션들
#
# 실험 갈래 (release_gate.Arm)
#   C0  : Defender 없음 (기준선)
#   C1  : 공개 전 Defender, 누적 State 기준
#   C1S : 공개 전 Defender, 매 턴 빈 State 기준 (무상태 대조군 — POC 보고서 실험 7)
#
# 위험 계산 백엔드 (Backend)
#   RISK_DEFENDER : 이유진 파트 risk_defender를 그대로 호출 (5개 QI, 기준 2개)
#   ONTOLOGY      : ontology 패키지의 계층으로 k를 세고 D1 Stage 11 방식으로 방어 (8개 QI, 하한 k_floor)
#   한 run은 백엔드 하나만 쓴다. 두 방식을 비교하려면 experiment_id를 달리해 두 번 돌린다.
#
# 같은 target을 여러 갈래로 돌릴 때
#   갈래마다 State·Fuzzer 진행 상태·난수 생성기를 새로 만든다. 난수 seed는 갈래와 무관하게 같은 값을 쓴다
#   (D1 Stage 15: 같은 반복에는 조건이 달라도 같은 seed). 첫 턴은 모든 갈래가 같은 Seed에서 출발하고,
#   그 뒤는 공개된 내용이 달라지면서 갈린다.
#
# 무거운 객체(기준 집단 30만 명, 색인, LLM client)는 밖에서 한 번 만들어 넘겨받는다.
# 이 파일은 .env나 파일 경로를 직접 읽지 않으므로, 테스트에서는 가짜 client·작은 기준 집단을 넣어 그대로 돌릴 수 있다.

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from adapters.fuzzer_adapter import FUZZER_SYSTEM_PROMPT_FILE, FuzzerAdapter, FuzzerConfig
from adapters.ontology_risk import OntologyRiskSession, build_population_index
from adapters.risk_adapter import ReferencePopulation, RiskBackend, RiskConfig, RiskSession
from attack.registry import SeedRegistry
from attack.selector import SeedSelector
from core.config import PROJECT_ROOT
from evaluation_v1.log_schema_v1 import KST
from normalizer.normalizer import Normalizer
from normalizer.vocabulary import ReferenceVocabulary
from ontology.index import PopulationIndex
from ontology.normalizer import OntologyNormalizer
from orchestrator.release_gate import Arm, Condition, GateConfig, ObservationNormalizer, ReleaseGate
from orchestrator.rewriter import ResponseRewriter, RewriterConfig
from orchestrator.run_log import RunLogger
from orchestrator.session import SessionParts, SessionResult, SessionRunner, TurnRecord
from qi_extractor.extractor import ExtractorConfig, QIExtractor
from target_llm.responder import TargetConfig, TargetResponder
from target_llm.retriever import Retriever

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs"
DEFAULT_ARMS = (Arm(Condition.C0), Arm(Condition.C1))


class Backend(str, Enum):
    RISK_DEFENDER = "risk_defender"
    ONTOLOGY = "ontology"


@dataclass(frozen=True)
class ExperimentConfig:
    """
    experiment_id      → 결과 폴더 이름이자 evaluation 로그의 experiment_id
    target_ids         → 대상 persona 목록
    arms               → 돌릴 실험 갈래 (C0, C1, C1S)
    backend            → 위험 계산 백엔드
    max_turns          → 세션당 Target 호출 상한
    rng_seed           → Seed 선택 난수 (갈래와 무관하게 같은 값)
    risk               → 위험 기준 (risk_threshold, generalize_threshold, tau_commit). Fuzzer 피드백도 risk_threshold를 쓴다
    gate               → 공개 게이트 설정 (고쳐 쓰기 횟수, 거절 문장)
    max_stale_turns    → k 변화 없는 턴이 이만큼 이어지면 새 Seed
    target / extractor / rewriter → 각 LLM 호출의 prompt 버전·temperature
    output_root        → 결과를 둘 상위 폴더 (기본 <프로젝트>/runs)
    """
    experiment_id: str
    target_ids: tuple[str, ...]
    arms: tuple[Arm, ...] = DEFAULT_ARMS
    backend: Backend = Backend.RISK_DEFENDER
    max_turns: int = 5
    rng_seed: int = 42
    risk: RiskConfig = RiskConfig()
    gate: GateConfig = GateConfig()
    max_stale_turns: int | None = 2
    target: TargetConfig = TargetConfig()
    extractor: ExtractorConfig = ExtractorConfig()
    rewriter: RewriterConfig = RewriterConfig()
    output_root: Path = field(default=DEFAULT_OUTPUT_ROOT)

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id가 비어 있습니다.")
        if not self.target_ids:
            raise ValueError("target_ids가 비어 있습니다.")
        if not self.arms:
            raise ValueError("arms가 비어 있습니다.")
        labels = [arm.label for arm in self.arms]
        if len(set(labels)) != len(labels):
            raise ValueError(f"같은 실험 갈래가 두 번 들어 있습니다: {labels}")
        if self.max_turns < 1:
            raise ValueError("max_turns는 1 이상이어야 합니다.")

    @property
    def run_dir(self) -> Path:
        return Path(self.output_root) / self.experiment_id


def default_experiment_id() -> str:
    return "poc_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class RiskEngine:
    """
    백엔드 하나를 run에서 쓸 수 있게 묶은 것.

    normalizer   → 추출 관찰을 이 백엔드의 값 형식으로 바꾸는 정규화기
    new_session  → (target_id, session_id) → 빈 위험 계산 세션
    description  → run_config에 남길 설명 (온톨로지 버전·해시, 기준 집단 매핑 결과 등)
    """
    backend: Backend
    normalizer: ObservationNormalizer
    new_session: Callable[[str, str], RiskBackend]
    description: dict[str, Any]


def build_risk_engine(
    backend: Backend,
    population: ReferencePopulation,
    risk: RiskConfig,
    *,
    vocabulary: ReferenceVocabulary | None = None,
    index: PopulationIndex | None = None,
) -> RiskEngine:
    """
    input : backend, population, risk,
            vocabulary / index (이미 만들어 둔 것이 있으면 다시 만들지 않는다. 30만 명 기준 각각 수 초)
    output : RiskEngine
    """
    if backend is Backend.ONTOLOGY:
        index = index or build_population_index(population)
        ontology = index.ontology
        return RiskEngine(
            backend=backend,
            normalizer=OntologyNormalizer(ontology, index.parser),
            new_session=lambda target_id, session_id: OntologyRiskSession(index, target_id, session_id, risk),
            description={
                "name": OntologyRiskSession.backend_name,
                "qi_in_k": 8,
                "defender_policy": "d1_one_parent_frontier (k_floor = min(k_req, k_before))",
                "ontology_version": ontology.version,
                "ontology_hash": ontology.content_hash,
                "ontology_sources": ontology.sources,
                "ontology_nodes": len(ontology),
                "reference_mapping": index.report.to_log_dict(),
            },
        )

    vocabulary = vocabulary or ReferenceVocabulary.from_population(population.records)
    return RiskEngine(
        backend=backend,
        normalizer=Normalizer(vocabulary),
        new_session=lambda target_id, session_id: RiskSession(population, target_id, session_id, risk),
        description={
            "name": RiskSession.backend_name,
            "qi_in_k": 5,
            "defender_policy": (f"risk_defender: k > {risk.generalize_threshold} 허용 / "
                                f"{risk.risk_threshold} < k <= {risk.generalize_threshold} 일반화 시도 / "
                                f"k <= {risk.risk_threshold} 억제"),
        },
    )


def run_experiment(
    config: ExperimentConfig,
    *,
    client: Any,
    population: ReferencePopulation,
    retriever: Retriever,
    registry: SeedRegistry,
    settings_description: dict[str, Any] | None = None,
    engine: RiskEngine | None = None,
    on_turn: Callable[[str, Arm, TurnRecord], None] | None = None,
) -> list[SessionResult]:
    """
    input : config, client (core LLMClient 또는 같은 모양의 객체), population (기준 집단),
            retriever (Target 검색기), registry (Seed 목록),
            settings_description (core Settings.describe() — run_config 기록용, 키 미포함),
            engine (없으면 config.backend로 만든다), on_turn (화면 출력 등 추가 처리)
    output : 세션 결과 목록 (target 순서 × 갈래 순서)
    """
    engine = engine or build_risk_engine(config.backend, population, config.risk)
    extractor = QIExtractor(client, config.extractor)
    parts = SessionParts(
        target=TargetResponder(retriever, client, config.target),
        extractor=extractor,
        normalizer=engine.normalizer,
        gate=ReleaseGate(extractor, engine.normalizer, ResponseRewriter(client, config.rewriter), config.gate),
    )

    model = getattr(client, "model", None)
    logger = RunLogger(
        config.run_dir,
        experiment_id=config.experiment_id,
        risk_config=config.risk,
        population_size=population.size,
        model=model,
        generation_config=f"target={config.target.prompt_version};temp={config.target.temperature}",
    )
    logger.write_config(_describe_run(config, population, retriever, registry, model, settings_description, engine))

    fuzzer_config = FuzzerConfig(risk_threshold=config.risk.risk_threshold,
                                 max_stale_turns=config.max_stale_turns)
    results: list[SessionResult] = []
    for target_id in config.target_ids:
        for arm in config.arms:
            session_id = f"{config.experiment_id}-{target_id}-{arm.label}"
            runner = SessionRunner(
                parts,
                engine.new_session(target_id, session_id),
                FuzzerAdapter(SeedSelector(registry, rng_seed=config.rng_seed), client, fuzzer_config),
                arm,
            )

            def handle(record: TurnRecord, target_id: str = target_id, arm: Arm = arm) -> None:
                logger.log_turn(target_id, arm, record)
                if on_turn is not None:
                    on_turn(target_id, arm, record)

            result = runner.run(target_id, max_turns=config.max_turns, on_turn=handle)
            logger.log_session_end(result)
            results.append(result)
    return results


# ──────────────────────────────────────────────────────────────
# run 설정 기록
# ──────────────────────────────────────────────────────────────

def _describe_run(config: ExperimentConfig, population: ReferencePopulation, retriever: Retriever,
                  registry: SeedRegistry, model: str | None,
                  settings_description: dict[str, Any] | None, engine: RiskEngine) -> dict[str, Any]:
    """재현에 필요한 설정을 한곳에 모은다 (D1: 모든 run에 version 집합 기록)."""
    return {
        "experiment_id": config.experiment_id,
        "started_at": datetime.now(KST).isoformat(),
        "target_ids": list(config.target_ids),
        "arms": [arm.label for arm in config.arms],
        "backend": engine.description,
        "max_turns": config.max_turns,
        "rng_seed": config.rng_seed,
        "risk": {
            "risk_threshold": config.risk.risk_threshold,
            "generalize_threshold": config.risk.generalize_threshold,
            "k_req": config.risk.k_req,
            "tau_commit": config.risk.tau_commit,
        },
        "gate": {
            "max_defense_rounds": config.gate.max_defense_rounds,
            "refusal_text": config.gate.refusal_text,
            "adopt_instructed_value": config.gate.adopt_instructed_value,
        },
        "fuzzer": {
            "max_stale_turns": config.max_stale_turns,
            "system_prompt_file": str(FUZZER_SYSTEM_PROMPT_FILE.relative_to(PROJECT_ROOT)),
            "system_prompt_sha256": _sha256(FUZZER_SYSTEM_PROMPT_FILE),
        },
        "population": {
            "mode": population.mode.value,
            "size": population.size,
            "base_path": _relative(population.base_path),
            "synth_path": _relative(population.synth_path) if population.synth_path else None,
        },
        "retriever": {"name": retriever.name, "version": retriever.version},
        "prompts": {
            "target": config.target.prompt_version,
            "extractor": config.extractor.prompt_version,
            "rewriter": config.rewriter.prompt_version,
        },
        "temperature": {
            "target": config.target.temperature,
            "extractor": config.extractor.temperature,
            "rewriter": config.rewriter.temperature,
        },
        "seed_registry": {
            "mode": registry.mode.value,
            "sources": [{**vars(source), "path": _relative(source.path)} for source in registry.sources],
        },
        "model": model,
        "llm_settings": settings_description,
    }


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _relative(path: str) -> str:
    """프로젝트 안의 경로면 루트 기준 상대 경로로 바꾼다 (기록에 개인 폴더 경로를 남기지 않기 위함)."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return Path(path).name
