# Target → Extractor → Normalizer → Risk 연결 확인: python -m adapters --target pers1 "질문"
#
# 한 턴을 끝까지 계산해 보고 출력만 한다 (State commit 없음, 응답 재작성 없음).
#   Target 응답(raw candidate) → QI 추출 → 정규화 → 공개했을 때 k (project) → Defender 판정 (defend)
# 기준 집단 30만 명 적재에 수 초, LLM 호출 2회가 걸린다.

from __future__ import annotations

import argparse
import time

from adapters.risk_adapter import (
    PopulationMode,
    RiskConfig,
    RiskSession,
    load_reference_population,
)
from core.llm_client import build_client
from normalizer.normalizer import Normalizer
from normalizer.vocabulary import ReferenceVocabulary
from qi_extractor.extractor import QIExtractor
from target_llm.corpus import PersonaCorpus
from target_llm.responder import TargetResponder
from target_llm.retriever import PersonaScopeRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="한 턴 위험 계산 확인 (commit 없음)")
    parser.add_argument("query", help="Target에 보낼 질문")
    parser.add_argument("--target", default="pers1")
    parser.add_argument("--population", choices=[m.value for m in PopulationMode], default="expanded")
    parser.add_argument("--threshold", type=int, default=RiskConfig().risk_threshold)
    args = parser.parse_args()

    started = time.perf_counter()
    population = load_reference_population(PopulationMode(args.population))
    vocabulary = ReferenceVocabulary.from_population(population.records)
    print(f"------ 기준 집단 {population.size:,}명 ({population.mode.value}) "
          f"적재 {time.perf_counter() - started:.1f}s ------")

    session = RiskSession(population, args.target, "check", RiskConfig(risk_threshold=args.threshold))
    normalizer = Normalizer(vocabulary)

    with build_client() as client:
        target = TargetResponder(PersonaScopeRetriever(PersonaCorpus.load()), client)
        extractor = QIExtractor(client)
        output = target.respond(args.target, args.query)
        extraction = extractor.extract(args.query, output.raw_candidate_response, source_turn=1,
                                       response_truncated=output.response_truncated)

    print("------ Target 응답 (raw candidate) ------")
    print(output.raw_candidate_response)

    print("------ 추출 → 정규화 ------")
    normalized = normalizer.normalize_all(extraction.observations)
    for obs in normalized:
        extra = f" (다른 표기: {', '.join(obs.alternatives)})" if obs.alternatives else ""
        extra += f" (후보: {', '.join(obs.candidates)})" if obs.candidates else ""
        print(f"[{obs.attribute}] {obs.raw_value} / en={obs.value_en} → "
              f"{obs.status.value}:{obs.canonical_value} ({obs.reason.value}){extra}")
    for rej in extraction.rejected:
        print(f"[추출 거부: {rej.reason.value}] {rej.detail}")

    projection = session.project(normalized)
    defense = session.defend(normalized)
    print("------ 위험 계산 (State 변경 없음) ------")
    print(f"k: {projection.k_before:,} → {projection.k_after:,} / 위험(k<={args.threshold}): {projection.risky} "
          f"/ 일반화 시도 기준 k<={session.config.generalize_threshold}")
    print(f"Defender: {defense.action} / 최종 k {defense.k_final:,}")
    for oid, attr, old, new in defense.generalized:
        print(f"    일반화 {oid} [{attr}] {old} → {new}")
    for oid in defense.suppressed_ids:
        print(f"    억제 {oid}")
    print(f"State(commit 전): {session.committed_qis} / 현재 k {session.k_current:,}")
    print("------")


if __name__ == "__main__":
    main()
