"""임계값(threshold) 배치 실험 스크립트.

300명 전체 타겟 × 여러 threshold 값으로 Defender 동작을 측정하고
최적 threshold를 제안한다. population은 기본 300,000개 사용.

사용법:
    python3 scripts/threshold_experiment.py
    python3 scripts/threshold_experiment.py --no-expanded  # 원본 300개만
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from risk_defender.loader import load_expanded_population, load_population
from risk_defender.schema import DEFAULT_QI_ORDER

DATA_PATH = BASE_DIR / "risk_defender" / "data" / "user_bot_profiles_300.json"
SYNTH_PATH = BASE_DIR / "risk_defender" / "data" / "user_bot_profiles_300000.json"
OUT_DIR = BASE_DIR / "risk_defender" / "outputs"

THRESHOLDS = [3, 5, 10, 15, 20, 30, 50]
QI_ORDER = ["LOC", "OCCP", "AGE", "EDU", "REL"]

# AGE 일반화 계층
AGE_PARENT = {v: "adult" for v in ["20s", "30s", "40s", "50s", "60s"]}
AGE_PARENT.update({v: "senior" for v in ["70s", "80s", "90s"]})
AGE_PARENT["teens"] = "young"

# EDU 일반화 계층
EDU_PARENT = {v: "higher_education" for v in ["Master's", "PhD", "College Degree"]}
EDU_PARENT.update({v: "non_advanced_degree" for v in ["HS Diploma", "In College/HS Diploma"]})

# REL 일반화 계층
REL_PARENT = {v: "partnered" for v in ["married", "engaged", "in a relationship"]}
REL_PARENT.update({v: "not_partnered" for v in ["single", "divorced", "widowed"]})


class PopulationIndex:
    """각 QI 값별로 인덱싱해서 k를 빠르게 계산."""

    def __init__(self, population):
        # {qi_type: {value: set(person_id)}}
        self.index: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        self.all_ids: set = {p.person_id for p in population}

        for p in population:
            for qi_type, val in p.qis.items():
                key = str(val).strip().casefold()
                self.index[qi_type][key].add(p.person_id)

        # 일반화 값 포함 (예: "adult" → 20s/30s/... 전부 포함)
        self._add_generalized("AGE", AGE_PARENT)
        self._add_generalized("EDU", EDU_PARENT)
        self._add_generalized("REL", REL_PARENT)
        self._add_loc_country()

    def _add_generalized(self, qi_type: str, parent_map: dict):
        for child, parent in parent_map.items():
            child_ids = self.index[qi_type].get(child.casefold(), set())
            self.index[qi_type][parent.casefold()] |= child_ids

    def _add_loc_country(self):
        for val, ids in list(self.index["LOC"].items()):
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if len(parts) >= 2:
                country = parts[-1].casefold()
                self.index["LOC"][country] |= ids

    def calculate_k(self, qis: dict[str, str]) -> int:
        if not qis:
            return len(self.all_ids)
        candidate_ids = None
        for qi_type, val in qis.items():
            key = str(val).strip().casefold()
            ids = self.index[qi_type].get(key, set())
            candidate_ids = ids if candidate_ids is None else candidate_ids & ids
        return len(candidate_ids) if candidate_ids is not None else 0


def generalize_loc(val: str) -> str | None:
    parts = [p.strip() for p in val.split(",") if p.strip()]
    return parts[-1] if len(parts) >= 2 else None


def generalize_value(qi_type: str, val: str) -> str | None:
    if qi_type == "LOC":
        return generalize_loc(val)
    if qi_type == "AGE":
        return AGE_PARENT.get(val)
    if qi_type == "EDU":
        return EDU_PARENT.get(val)
    if qi_type == "REL":
        return REL_PARENT.get(val)
    return None


def defend(index: PopulationIndex, committed: dict, qi_type: str, val: str, threshold: int) -> tuple[str, dict]:
    """단일 QI에 대해 defend. (action, 새 committed) 반환."""
    test = {**committed, qi_type: val}
    k = index.calculate_k(test)

    if k > threshold:
        return "allow", test

    # 일반화 시도
    gen_val = generalize_value(qi_type, val)
    if gen_val:
        gen_test = {**committed, qi_type: gen_val}
        if index.calculate_k(gen_test) > threshold:
            return "generalize", gen_test

    # suppress
    return "suppress", committed


def run_all(index: PopulationIndex, targets, threshold: int) -> dict:
    action_counts = {"allow": 0, "generalize": 0, "suppress": 0}
    final_ks: list[int] = []
    committed_qi_counts: list[int] = []
    per_row: list[dict] = []

    for target in targets:
        committed: dict = {}
        for qi_type in QI_ORDER:
            val = target.qis.get(qi_type)
            if val is None:
                continue
            action, committed = defend(index, committed, qi_type, str(val), threshold)
            action_counts[action] += 1
            per_row.append({
                "target_id": target.person_id,
                "threshold": threshold,
                "qi_type": qi_type,
                "action": action,
                "k_after": index.calculate_k(committed),
            })

        final_ks.append(index.calculate_k(committed))
        committed_qi_counts.append(len(committed))

    total = sum(action_counts.values())
    return {
        "threshold": threshold,
        "total_decisions": total,
        "allow_rate": action_counts["allow"] / total,
        "generalize_rate": action_counts["generalize"] / total,
        "suppress_rate": action_counts["suppress"] / total,
        "mean_final_k": mean(final_ks),
        "median_final_k": median(final_ks),
        "min_final_k": min(final_ks),
        "mean_committed_qis": mean(committed_qi_counts),
        "per_row": per_row,
    }


def score(result: dict) -> float:
    """utility(정보 유지율) - suppress 패널티."""
    utility = result["allow_rate"] + result["generalize_rate"] * 0.5
    return utility - result["suppress_rate"] * 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-expanded", action="store_true", help="원본 300개만 사용 (기본: 300,000개)")
    args = parser.parse_args()

    print("Population 로딩 중...")
    if args.no_expanded:
        population = load_population(DATA_PATH)
        pop_label = "300"
    else:
        if not SYNTH_PATH.exists():
            print("합성 데이터 없음. 먼저 scripts/generate_synth_population.py 실행 필요.")
            sys.exit(1)
        population = load_expanded_population(DATA_PATH, synth_path=SYNTH_PATH)
        pop_label = "300,000"
    print(f"Population: {len(population):,}명")

    print("인덱스 구축 중...")
    index = PopulationIndex(population)

    targets = [p for p in population
               if p.person_id.startswith("pers") and p.person_id[4:].isdigit()
               and int(p.person_id[4:]) <= 300]
    print(f"타겟: {len(targets)}명, threshold 후보: {THRESHOLDS}\n")

    results = []
    all_rows: list[dict] = []

    for threshold in THRESHOLDS:
        result = run_all(index, targets, threshold)
        results.append(result)
        all_rows.extend(result.pop("per_row"))
        s = score(result)
        print(
            f"threshold={threshold:2d} | "
            f"allow={result['allow_rate']:.1%}  "
            f"gen={result['generalize_rate']:.1%}  "
            f"supp={result['suppress_rate']:.1%} | "
            f"median_k={result['median_final_k']:.0f}  "
            f"min_k={result['min_final_k']}  "
            f"avg_QI={result['mean_committed_qis']:.2f} | "
            f"score={s:.3f}"
        )

    best = max(results, key=score)
    print(f"\n최적 threshold: {best['threshold']}")
    print(f"  suppress율:   {best['suppress_rate']:.1%}")
    print(f"  generalize율: {best['generalize_rate']:.1%}")
    print(f"  allow율:      {best['allow_rate']:.1%}")
    print(f"  median final k: {best['median_final_k']:.0f}")
    print(f"  min final k:    {best['min_final_k']}")
    print(f"  평균 committed QI 수: {best['mean_committed_qis']:.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = OUT_DIR / "threshold_experiment_summary.csv"
    detail_path = OUT_DIR / "threshold_experiment_detail.csv"

    summary_fields = [k for k in results[0].keys()]
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(results)

    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n결과 저장: {summary_path}")
    print(f"상세 데이터: {detail_path}")
    print(f"\n결론: population={pop_label}, 권장 threshold = {best['threshold']}")


if __name__ == "__main__":
    main()
