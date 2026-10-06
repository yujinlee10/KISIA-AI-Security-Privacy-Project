# 기준 집단 매핑 점검: python -m ontology [--population base|expanded] [--show-review]
#
# D1 Stage 3.2가 Main 전에 요구하는 두 가지를 출력한다.
#   1) 기준 집단이 온톨로지로 100% 풀리는지 (풀지 못한 값 목록)
#   2) QI·단계별로 사람이 어떻게 나뉘는지 (계층이 쓸모 있는지 보는 사전 점검)
# --show-review를 주면 직업 대응표에서 사람 검토가 필요한 행(confidence M·L)도 함께 보여 준다.
# LLM을 부르지 않는다.

from __future__ import annotations

import argparse
import time
from statistics import median

from adapters.ontology_risk import build_population_index
from adapters.risk_adapter import PopulationMode, load_reference_population
from core.schemas import QI_TYPES
from ontology.isco import load_occupation_table


def main() -> None:
    parser = argparse.ArgumentParser(description="온톨로지 기준 집단 매핑 점검")
    parser.add_argument("--population", choices=[m.value for m in PopulationMode], default="base",
                        help="base: 원본 300명 (기본) / expanded: 30만 명")
    parser.add_argument("--show-review", action="store_true", help="직업 대응표의 검토 대상 행 출력")
    args = parser.parse_args()

    population = load_reference_population(PopulationMode(args.population))
    started = time.perf_counter()
    index = build_population_index(population)
    ontology = index.ontology

    print(f"------ 온톨로지 {ontology.version} / node {len(ontology):,}개 / 해시 {ontology.content_hash[:16]}… ------")
    print(f"------ 기준 집단 {index.size:,}명 ({population.mode.value}) / 색인 생성 {time.perf_counter() - started:.1f}s "
          f"/ 전원 매핑: {'예' if index.report.fully_mapped else '아니오'} ------")

    for attribute in QI_TYPES:
        report = index.report.attributes[attribute]
        levels = ", ".join(f"{level} {count:,}" for level, count in sorted(report.by_level.items()))
        print(f"\n[{attribute}] 매핑 {report.mapped:,}/{report.total:,} ({report.coverage:.1%}) "
              f"/ 쓰인 node {report.distinct_nodes}개 / 단계별 인원: {levels}")
        for value, count in sorted(report.unmapped_values.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    풀지 못함: {value!r} ({count:,}명)")

        # 단계별로 node 하나에 몇 명이 속하는지 (값 하나만 공개됐을 때의 k 분포)
        by_level: dict[str, list[int]] = {}
        for node in ontology.nodes(attribute):
            count = index.node_count(node.node_id)
            if count and not node.is_root:
                by_level.setdefault(node.level, []).append(count)
        for level, counts in sorted(by_level.items()):
            print(f"    {level:<12} node {len(counts):>4}개 / node당 인원 최소 {min(counts):,} · 중앙 {median(counts):,.0f} "
                  f"· 최대 {max(counts):,}")

    if args.show_review:
        table = load_occupation_table()
        rows = [a for a in table.aliases.values() if a.confidence != "H"]
        print(f"\n------ 직업 대응표 검토 대상 {len(rows)}행 / 전체 {len(table.aliases)}행 ({table.path}) ------")
        for alias in sorted(rows, key=lambda a: (a.confidence, a.alias)):
            print(f"  [{alias.confidence}] {alias.alias:<40} → {alias.isco_code:<5} {alias.note}")
    print("------")


if __name__ == "__main__":
    main()
