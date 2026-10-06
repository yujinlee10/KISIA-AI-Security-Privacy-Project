# 사후 검증 실행: python -m validation <experiment_id> [<experiment_id> …]
#
# 실행 폴더의 run_config.json에서 백엔드·기준 집단·k_req를 읽어 그 실행에 맞는 검증기를 만든다.
# LLM을 부르지 않는다. 기준 집단 30만 명이면 준비에 수 초가 걸린다.

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.ontology_risk import build_population_index
from adapters.risk_adapter import PopulationMode, ReferencePopulation, load_reference_population
from core.config import PROJECT_ROOT
from validation.run import validate_run
from validation.validator import OntologyValidator, RiskDefenderValidator, Validator

DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description="실행 결과를 정답과 대조해 k_val을 계산 (LLM 호출 없음)")
    parser.add_argument("experiment_ids", nargs="+", help="runs/ 아래 실행 폴더 이름")
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    args = parser.parse_args()

    populations: dict[str, ReferencePopulation] = {}
    validators: dict[tuple[str, str], Validator] = {}

    for experiment_id in args.experiment_ids:
        run_dir = Path(args.runs_root) / experiment_id
        config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
        mode = (config.get("population") or {}).get("mode", "expanded")
        backend = (config.get("backend") or {}).get("name", "risk_defender")
        k_req = (config.get("risk") or {}).get("k_req", 4)

        if mode not in populations:
            populations[mode] = load_reference_population(PopulationMode(mode))
        if (mode, backend) not in validators:
            population = populations[mode]
            validators[(mode, backend)] = (
                OntologyValidator(build_population_index(population), population)
                if backend == "ontology_d1" else RiskDefenderValidator(population))

        sessions = validate_run(run_dir, validators[(mode, backend)], k_req)
        print(f"\n====== {experiment_id} (백엔드 {backend}, 기준 집단 {populations[mode].size:,}명, k_req {k_req}) ======")
        print(f"{'target':<10}{'갈래':<5}{'턴':>3}  {'최소 k_run':>10}  {'최소 k_val':>10}  "
              f"{'첫 위반(k_run)':>12}  {'첫 위반(k_val)':>12}  틀린 값이 들어간 QI")
        for s in sessions:
            print(f"{s.target_id:<10}{s.arm:<5}{s.turns:>3}  {s.k_run_min:>10,}  {s.k_val_min:>10,}  "
                  f"{str(s.runtime_first_violation_turn or '-'):>12}  "
                  f"{str(s.validated_first_violation_turn or '-'):>12}  {', '.join(s.incorrect_qis) or '-'}")
        print(f"결과: {run_dir}/validation.jsonl, experiment_log_validated.csv")


if __name__ == "__main__":
    main()
