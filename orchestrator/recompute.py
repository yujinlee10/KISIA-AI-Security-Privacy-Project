# 기록된 응답으로 다시 계산하기: python -m orchestrator.recompute --source run_multi10
#
# 코드(정규화 규칙, 직업 대응표, 추출 검증, Defender 판정)를 고친 뒤 "같은 대화였다면 결과가 어떻게 달라지는가"를
# LLM을 거의 부르지 않고 확인한다. 원래 실행의 trace에서 다음을 꺼내 그대로 다시 넣는다.
#
#   질문           : 첫 갈래가 던진 질문 순서 (다시 만들지 않는다)
#   Target 원 응답 : 그 질문에 대한 원 응답 (Target을 부르지 않는다)
#   원 응답 추출   : 추출기 LLM이 냈던 출력 원문 (다시 부르지 않고, 지금 코드로 다시 해석·검증한다)
#
# 다시 하는 것은 정규화, 위험 계산, Defender 판정, State 반영이다. 전부 지금 코드로 한다.
#
# Defender가 개입해 응답을 고쳐 써야 하는 턴의 처리
#   원래 실행과 같은 지시(같은 값을 지우거나 넓히라는 지시)가 나오면 기록된 재작성 결과를 그대로 쓴다.
#   고쳐 쓴 응답의 재추출 출력도 기록에 있으면 그대로 쓴다 (이 기능이 들어간 뒤의 실행부터 trace에 남는다).
#   기록에 없는 경우에만 LLM을 부른다:
#     · 설정을 바꿔 Defender의 판정이 달라졌을 때 (재작성 + 재추출)
#     · 예전 trace라 재추출 출력이 없을 때 (재추출만. 10명 × 12턴 실행에서 약 10번)
#   --offline을 주면 그런 턴은 부르지 않고 거절로 처리한다. C1의 결과가 그만큼 보수적으로 나온다. C0는 영향이 없다.
#
# 설정을 바꿔 다시 계산하기 (같은 대화에 다른 기준을 적용)
#   --threshold N       위험 기준 (k_req = N + 1).   예: --threshold 4 → k_req 5 (D1 기준)
#   --backend NAME      risk_defender / ontology
#   --min-confidence N  추출 확신도가 N 미만인 관찰은 State에 넣지 않는다
#   --no-residual-check 재작성 뒤 잔여 단서 검사를 끈다
#   결과 폴더 이름에 바꾼 설정이 붙는다 (예: run_multi10__recomputed_t4).
#
# 쓸 수 없는 경우
#   질문 생성(Fuzzer 프롬프트, 변형 검사)이나 Target 프롬프트를 바꿨을 때. 그때는 질문·응답이 달라지므로 새로 돌려야 한다.
#   원래 실행이 --paired가 아니었으면 갈래마다 질문이 다르다. 이 명령은 대상마다 첫 갈래의 질문·응답만 쓴다.
#
# 결과는 runs/<source>__recomputed/ (또는 --experiment-id)에 원래 실행과 같은 형식으로 남는다.
# 이어서 python -m validation, python -m orchestrator.report를 그대로 쓸 수 있다.

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Sequence

from adapters.replay_fuzzer import ReplayFuzzer
from adapters.risk_adapter import PopulationMode, ReferencePopulation, load_reference_population
from core.llm_client import LLMClient, LLMResponse
from dataclasses import replace

from orchestrator.experiment import (
    DEFAULT_OUTPUT_ROOT,
    Backend,
    ExperimentConfig,
    RiskEngine,
    build_risk_engine,
    build_run_logger,
)
from orchestrator.release_gate import ARM_LABELS, Arm, ReleaseGate
from orchestrator.replay import config_from_source, load_source_config
from orchestrator.rewriter import ResponseRewriter, RewriteAction, RewriteInstruction
from orchestrator.rewriter import build_user_prompt as build_rewrite_prompt
from orchestrator.run_log import TRACE_FILE_NAME
from orchestrator.session import SessionParts, SessionResult, SessionRunner
from orchestrator.trace_reader import arm_of, iter_sessions
from qi_extractor.extractor import RESPONSE_SECTION_HEADER, QIExtractor
from target_llm.corpus import PersonaCorpus
from target_llm.responder import TargetOutput
from target_llm.retriever import PersonaScopeRetriever, Retriever

RECOMPUTED_SUFFIX = "__recomputed"


class RecordedDialogue:
    """대상 한 명의 기록: 질문 순서, 질문별 원 응답, 원 응답별 추출기 출력."""

    def __init__(self, target_id: str, turns: Sequence[dict[str, Any]]) -> None:
        self.target_id = target_id
        self.queries = [
            {"turn": item["turn"], "query": item["attack"]["query"], "seed_id": item["attack"]["seed_id"],
             "seed_version": item["attack"].get("seed_version", ""), "objective": item["attack"]["objective"],
             "scenario_code": item["attack"]["scenario_code"], "target_qi": item["attack"]["target_qi"]}
            for item in turns
        ]
        self.targets = {item["attack"]["query"]: item["target"] for item in turns}
        self.rewrites: dict[str, str] = {}        # 재작성기에 보낸 요청 원문 → 고쳐 쓴 응답
        self.extractions = {
            item["target"]["raw_candidate_response"].strip(): item["raw_extraction"]["raw_output"]
            for item in turns if item.get("raw_extraction") and item["raw_extraction"].get("raw_output")
        }

    def add_rewrites(self, turns: Sequence[dict[str, Any]]) -> None:
        """
        한 세션(어느 갈래든)의 재작성 기록을 더한다. 재작성 요청은 (고칠 응답, 지시)로 정해지므로
        지금 계산에서 같은 요청이 나오면 그 결과를 다시 쓸 수 있다. 재추출 출력이 남아 있으면 함께 담는다.
        """
        for item in turns:
            text = item["target"]["raw_candidate_response"]
            for round_item in item["release"].get("rounds") or []:
                rewrite = round_item.get("rewrite")
                if not rewrite:
                    break
                instructions = [
                    RewriteInstruction(
                        observation_id=entry["observation_id"], attribute=entry["attribute"],
                        action=RewriteAction(entry["action"]), raw_value=entry["raw_value"],
                        evidence=tuple(entry.get("evidence") or ()), replacement=entry.get("replacement"),
                        replacement_text=entry.get("replacement_text"))
                    for entry in rewrite["instructions"]
                ]
                self.rewrites[build_rewrite_prompt(text, instructions)] = rewrite["text"]
                if round_item.get("extraction_raw_output"):
                    self.extractions[rewrite["text"].strip()] = round_item["extraction_raw_output"]
                text = rewrite["text"]


def load_dialogues(run_dir: str | Path, target_ids: Sequence[str] | None = None) -> list[RecordedDialogue]:
    """
    input : run_dir (원래 실행 폴더), target_ids (일부만 다시 계산할 때)
    output : 대상별 기록. 대상마다 질문을 직접 만든 갈래(첫 갈래)의 마지막으로 끝난 세션을 쓴다

    LLM 오류로 끝난 세션은 쓰지 않는다 (턴이 모자라다).
    """
    trace_path = Path(run_dir) / TRACE_FILE_NAME
    if not trace_path.exists():
        raise FileNotFoundError(f"trace 파일이 없습니다: {trace_path}")
    latest: dict[str, tuple[dict[str, Any], ...]] = {}
    followers: dict[str, list[tuple[dict[str, Any], ...]]] = {}
    for session in iter_sessions(trace_path):
        head = session.head
        if not session.finished or not session.turns or head.get("session_key") is not None:
            continue
        if target_ids is not None and head["target_id"] not in target_ids:
            continue
        if session.end.get("stop_reason") == "LLM_ERROR":
            continue
        if head.get("questions_from"):
            followers.setdefault(head["target_id"], []).append(session.turns)
            continue
        lead = latest.get(head["target_id"])
        # 같은 대상에서 질문을 직접 만든 갈래가 여럿이면(--paired 없이 돌린 실행) 처음 나온 갈래를 쓴다
        if lead is None or arm_of(lead[0]) == arm_of(head):
            latest[head["target_id"]] = session.turns
    dialogues = []
    for target_id, turns in latest.items():
        dialogue = RecordedDialogue(target_id, turns)
        queries = [item["attack"]["query"] for item in turns]
        for other in [turns, *followers.get(target_id, [])]:
            # 같은 질문 순서를 받은 세션의 재작성만 쓴다 (이어 돌리기 전의 옛 세션은 질문이 다를 수 있다)
            if [item["attack"]["query"] for item in other] == queries[:len(other)]:
                dialogue.add_rewrites(other)
        dialogues.append(dialogue)
    return dialogues


class RecordedTarget:
    """TargetResponder 대역: 기록된 원 응답을 돌려준다. 문맥 정보는 지금 검색기로 다시 만든다 (LLM 호출 없음)."""

    def __init__(self, retriever: Retriever, dialogues: Sequence[RecordedDialogue]) -> None:
        self._retriever = retriever
        self._records = {(d.target_id, query): record for d in dialogues for query, record in d.targets.items()}

    def respond(self, target_id: str, query: str, **_: Any) -> TargetOutput:
        record = self._records[(target_id, query)]
        return TargetOutput(
            target_id=target_id, query=query, raw_candidate_response=record["raw_candidate_response"],
            retrieval=self._retriever.retrieve(target_id, query), rules_injected=record.get("rules_injected", 0),
            prompt_version=record.get("prompt_version", ""), requested_model=record.get("requested_model"),
            response_model=record.get("response_model"), stop_reason=record.get("stop_reason"),
            response_truncated=bool(record.get("response_truncated")), input_tokens=0, output_tokens=0,
            latency_ms=0.0, attempts=0, temperature=record.get("temperature"), max_tokens=record.get("max_tokens"),
        )


class RecordedExtractionClient:
    """
    추출기·재작성기가 쓰는 LLM client 대역.
      추출 요청   → 그 응답의 추출기 출력이 기록에 있으면 그것을 돌려준다 (해석·검증은 지금 코드가 다시 한다)
      재작성 요청 → 같은 요청(같은 응답, 같은 지시)의 결과가 기록에 있으면 그것을 돌려준다
      기록에 없는 호출 → inner가 있으면 실제 LLM, 없으면(offline) 빈 응답
    빈 응답은 재작성·추출 실패로 처리되어 그 턴이 거절로 끝난다.
    """

    def __init__(self, inner: Any | None, dialogues: Sequence[RecordedDialogue]) -> None:
        self._inner = inner
        self._outputs = {text: output for d in dialogues for text, output in d.extractions.items()}
        self._rewrites = {prompt: text for d in dialogues for prompt, text in d.rewrites.items()}
        self.model = getattr(inner, "model", None)
        self.recorded_hits = 0
        self.live_calls = 0

    def complete(self, user_prompt: str, *, system_prompt: str | None = None,
                 max_tokens: int | None = None, temperature: float | None = None) -> LLMResponse:
        if RESPONSE_SECTION_HEADER in user_prompt:
            response_text = user_prompt.split(RESPONSE_SECTION_HEADER, 1)[1].strip()
            recorded = self._outputs.get(response_text)
            if recorded is not None:
                self.recorded_hits += 1
                return _response(recorded, self.model)
        elif user_prompt in self._rewrites:
            self.recorded_hits += 1
            return _response(self._rewrites[user_prompt], self.model)
        if self._inner is None:
            return _response("", self.model)
        self.live_calls += 1
        return self._inner.complete(user_prompt, system_prompt=system_prompt, max_tokens=max_tokens,
                                    temperature=temperature)


def _response(text: str, model: str | None) -> LLMResponse:
    return LLMResponse(text=text, model=model, stop_reason="end_turn", input_tokens=0, output_tokens=0,
                       latency_ms=0.0, attempts=0)


def recompute(
    dialogues: Sequence[RecordedDialogue],
    config: ExperimentConfig,
    *,
    population: ReferencePopulation,
    retriever: Retriever,
    client: Any | None = None,
    engine: RiskEngine | None = None,
    source_config: dict[str, Any] | None = None,
) -> tuple[list[SessionResult], RecordedExtractionClient]:
    """
    input : dialogues (원래 실행의 대상별 기록), config (결과 폴더·갈래·백엔드·위험 기준),
            population, retriever, client (실제 LLM. None이면 offline), engine, source_config (기록용)
    output : (세션 결과 목록, 호출 통계를 가진 client 대역)
    """
    engine = engine or build_risk_engine(config.backend, population, config.risk)
    recorded_client = RecordedExtractionClient(client, dialogues)
    extractor = QIExtractor(recorded_client, config.extractor)
    parts = SessionParts(
        target=RecordedTarget(retriever, dialogues),
        extractor=extractor,
        normalizer=engine.normalizer,
        gate=ReleaseGate(extractor, engine.normalizer, ResponseRewriter(recorded_client, config.rewriter), config.gate),
    )
    logger = build_run_logger(config, recorded_client, population, replay_type="recomputed")
    logger.write_config({
        "experiment_id": config.experiment_id,
        "recomputed_from": (source_config or {}).get("experiment_id"),
        "offline": client is None,
        "target_ids": [d.target_id for d in dialogues],
        "arms": [arm.label for arm in config.arms],
        "max_turns": max((len(d.queries) for d in dialogues), default=0),
        "backend": engine.description,
        "risk": {"risk_threshold": config.risk.risk_threshold, "generalize_threshold": config.risk.generalize_threshold,
                 "k_req": config.risk.k_req, "tau_commit": config.risk.tau_commit},
        "gate": {"max_defense_rounds": config.gate.max_defense_rounds, "refusal_text": config.gate.refusal_text,
                 "adopt_instructed_value": config.gate.adopt_instructed_value,
                 "check_residual_evidence": config.gate.check_residual_evidence},
        "population": {"mode": population.mode.value, "size": population.size},
        "prompts": {"target": "(기록된 응답)", "extractor": config.extractor.prompt_version,
                    "rewriter": config.rewriter.prompt_version},
        "temperature": {"target": None, "extractor": config.extractor.temperature,
                        "rewriter": config.rewriter.temperature},
        "fuzzer": {"paired_questions": True, "shared_raw_responses": True},
        "model": recorded_client.model,
        "source_run_config": source_config,
    })

    results: list[SessionResult] = []
    for dialogue in dialogues:
        lead_label = config.arms[0].label
        for arm in config.arms:
            runner = SessionRunner(
                parts,
                engine.new_session(dialogue.target_id, f"{config.experiment_id}-{dialogue.target_id}-{arm.label}"),
                ReplayFuzzer(dialogue.queries, config.risk.risk_threshold, label="RECORDED"),
                arm,
            )
            extra = {"questions_from": lead_label} if arm is not config.arms[0] else None
            result = runner.run(
                dialogue.target_id, max_turns=len(dialogue.queries),
                on_turn=lambda record, arm=arm, extra=extra, target=dialogue.target_id:
                    logger.log_turn(target, arm, record, extra=extra))
            logger.log_session_end(result, extra=extra)
            results.append(result)
    return results, recorded_client


def apply_overrides(config: ExperimentConfig, *, threshold: int | None = None, backend: str | None = None,
                    min_confidence: int | None = None,
                    residual_check: bool | None = None) -> tuple[ExperimentConfig, str]:
    """
    input : config (원래 실행에서 가져온 설정), 바꿀 값들 (None이면 그대로 둔다)
    output : (바뀐 설정, 결과 폴더 이름에 붙일 꼬리표. 바꾼 것이 없으면 빈 문자열)
    """
    tags: list[str] = []
    risk, gate = config.risk, config.gate
    if threshold is not None and threshold != risk.risk_threshold:
        risk = replace(risk, risk_threshold=threshold, generalize_threshold=max(risk.generalize_threshold, threshold))
        tags.append(f"t{threshold}")
    if min_confidence is not None and min_confidence != risk.tau_commit:
        risk = replace(risk, tau_commit=min_confidence)
        tags.append(f"c{min_confidence}")
    if backend is not None and Backend(backend) is not config.backend:
        config = replace(config, backend=Backend(backend))
        tags.append("rd" if Backend(backend) is Backend.RISK_DEFENDER else "onto")
    if residual_check is not None and residual_check != gate.check_residual_evidence:
        gate = replace(gate, check_residual_evidence=residual_check)
        tags.append("noresidual")
    return replace(config, risk=risk, gate=gate), ("_" + "_".join(tags) if tags else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="기록된 질문·응답·추출 출력으로 지금 코드의 결과를 다시 계산")
    parser.add_argument("--source", required=True, help="원래 실행의 experiment_id (runs/ 아래 폴더 이름)")
    parser.add_argument("--arms", nargs="+", default=None, choices=ARM_LABELS, type=str.upper,
                        help="다시 계산할 갈래 (기본: 원래 실행의 갈래)")
    parser.add_argument("--target", nargs="*", default=None, help="일부 대상만 (기본: 전부)")
    parser.add_argument("--offline", action="store_true",
                        help="LLM을 전혀 부르지 않는다. Defender가 개입한 턴은 거절로 처리한다")
    parser.add_argument("--threshold", type=int, default=None,
                        help="위험 기준을 바꿔 다시 계산 (k_req = 값 + 1). 기본: 원래 실행과 같은 값")
    parser.add_argument("--backend", choices=[backend.value for backend in Backend], default=None,
                        help="위험 계산 백엔드를 바꿔 다시 계산. 기본: 원래 실행과 같은 백엔드")
    parser.add_argument("--min-confidence", type=int, default=None, choices=[1, 2, 3, 4, 5],
                        help="추출 확신도가 이 값보다 낮은 관찰은 State에 넣지 않는다")
    parser.add_argument("--no-residual-check", action="store_true", help="재작성 뒤 잔여 단서 검사를 끈다")
    parser.add_argument("--experiment-id", default=None,
                        help="결과 폴더 이름 (기본: <source>__recomputed 뒤에 바꾼 설정을 붙인다)")
    args = parser.parse_args()

    source_dir = DEFAULT_OUTPUT_ROOT / args.source
    source = load_source_config(source_dir)
    dialogues = load_dialogues(source_dir, args.target)
    if not dialogues:
        print(f"다시 계산할 세션이 없습니다 ({source_dir}). 끝까지 돈 세션이 있는지 확인하세요.")
        return
    arms = [Arm.parse(label) for label in (args.arms or source.get("arms") or ["C0", "C1"])]
    config = config_from_source(source, experiment_id=args.experiment_id or args.source + RECOMPUTED_SUFFIX,
                                arms=arms, output_root=DEFAULT_OUTPUT_ROOT)
    config, suffix = apply_overrides(config, threshold=args.threshold, backend=args.backend,
                                     min_confidence=args.min_confidence,
                                     residual_check=False if args.no_residual_check else None)
    if args.experiment_id is None and suffix:
        config = replace(config, experiment_id=config.experiment_id + suffix)
    if (config.run_dir / TRACE_FILE_NAME).exists():
        print(f"{config.run_dir}에 이미 결과가 있습니다. 섞이지 않도록 --experiment-id를 바꿔 주세요.")
        return

    started = time.perf_counter()
    population = load_reference_population(PopulationMode((source.get("population") or {}).get("mode", "expanded")))
    engine = build_risk_engine(config.backend, population, config.risk)
    retriever = PersonaScopeRetriever(PersonaCorpus.load())
    turns = sum(len(d.queries) for d in dialogues)
    print(f"------ 대상 {len(dialogues)}명 × 갈래 {len(arms)}개, 기록된 턴 {turns}개 / 기준 집단 {population.size:,}명 "
          f"/ 백엔드 {engine.description['name']} / 준비 {time.perf_counter() - started:.1f}s ------")
    print("------ " + ("offline: LLM을 부르지 않습니다. 기록에 없는 재작성·재추출이 필요한 턴은 거절로 처리합니다"
                       if args.offline else "기록에 없는 재작성·재추출이 필요할 때만 LLM을 부릅니다") + " ------")

    if args.offline:
        results, stats = recompute(dialogues, config, population=population, retriever=retriever, engine=engine,
                                   source_config=source)
    else:
        from core.config import get_settings
        with LLMClient(get_settings()) as client:
            results, stats = recompute(dialogues, config, population=population, retriever=retriever,
                                       client=client, engine=engine, source_config=source)

    k_req = config.risk.k_req
    print(f"\n{'대상':<10}{'갈래':<5}{'턴':>3}  {'최소 k_run':>10}  {'첫 위반(k_run)':>12}  거절 턴")
    for result in results:
        refusals = sum(1 for turn in result.turns if turn.release.path.value == "REFUSAL")
        print(f"{result.target_id:<10}{result.arm.label:<5}{len(result.turns):>3}  {result.k_min:>10,}  "
              f"{str(result.first_violation_turn(k_req) or '-'):>12}  {refusals}")
    print(f"\n기록을 다시 쓴 추출 {stats.recorded_hits}회 / 실제 LLM 호출 {stats.live_calls}회")
    print(f"결과 폴더: {config.run_dir}")
    print(f"다음 단계: python -m validation {config.experiment_id}")
    print(f"           python -m orchestrator.report {config.experiment_id}")


if __name__ == "__main__":
    main()
