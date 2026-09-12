import os
import json
import pandas as pd
from typing import Dict, Any

from log_schema import LogManager

# 어디서 실행하든(프로젝트 루트든 evaluation 폴더 안이든) 항상 이 파일 기준으로
# logs/, results/ 경로를 잡기 위한 기준 디렉토리
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "logs", "experiment_log.csv")
DEFAULT_RESULT_DIR = os.path.join(BASE_DIR, "results")


class BaselineEvaluator:
    def __init__(self, log_csv_path: str):
        if not os.path.exists(log_csv_path):
            # 아직 아무도 로그를 안 남겼어도 에러 없이 빈 로그 파일을 만들고 진행한다.

            print(
                f"[안내] 로그 파일이 없어서 새로 만듭니다: {log_csv_path}\n"
                f"       (아직 기록된 실험은 없습니다)"
            )
            LogManager(log_csv_path)

        self.df = pd.read_csv(log_csv_path)

        numeric_columns = ["turn", "k_before", "k_after", "threshold", "utility_score"]
        for col in numeric_columns:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        if "timestamp" in self.df.columns:
            self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], errors="coerce")

        bool_columns = ["risk_event", "generalized", "suppressed"]
        for col in bool_columns:
            if col in self.df.columns:
                self.df[col] = (
                    self.df[col]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .map({"true": True, "false": False, "1": True, "0": False})
                )

    # ------------------------------------------------------------------
    # 1. Security Evaluation
    # ------------------------------------------------------------------
    def compute_security_metrics(self) -> pd.DataFrame:
        rows = []
        for (exp_id, defender), group in self.df.groupby(["experiment_id", "defender"]):
            total_turns = len(group)
            risk_events = int(group["risk_event"].fillna(False).sum())
            risk_turns = group.loc[group["risk_event"] == True, "turn"]

            rows.append({
                "experiment_id": exp_id,
                "defender": defender,
                "total_turns": total_turns,
                "min_k_before": group["k_before"].min(),
                "min_k_after": group["k_after"].min(),
                "risk_event_count": risk_events,
                "threshold_reach_rate": round(risk_events / total_turns, 4) if total_turns else 0.0,
                "first_turn_to_risk": int(risk_turns.min()) if not risk_turns.empty else None,
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 2. Defender Evaluation
    # ------------------------------------------------------------------
    def compute_defender_metrics(self) -> pd.DataFrame:
        """
        defense_success_rate:
            '원래 위험했던 turn (k_before <= threshold)' 중에서
            방어 후 안전해진 turn (k_after > threshold)의 비율.
            (안전했던 turn까지 분모에 넣으면 효과가 희석되므로 제외)
        """
        rows = []
        for (exp_id, defender), group in self.df.groupby(["experiment_id", "defender"]):
            gen_rate = group["generalized"].fillna(False).mean()
            supp_rate = group["suppressed"].fillna(False).mean()

            valid = group.dropna(subset=["k_before", "k_after", "threshold"])
            if len(valid) > 0:
                risk_before = valid["k_before"] <= valid["threshold"]
                safe_after = valid["k_after"] > valid["threshold"]
                if risk_before.sum() > 0:
                    success_rate = round((safe_after & risk_before).sum() / risk_before.sum(), 4)
                else:
                    success_rate = None
            else:
                success_rate = None

            rows.append({
                "experiment_id": exp_id,
                "defender": defender,
                "generalization_rate": round(gen_rate, 4),
                "suppression_rate": round(supp_rate, 4),
                "defense_success_rate": success_rate,
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 3. Utility Evaluation
    # ------------------------------------------------------------------
    def compute_utility_metrics(self) -> pd.DataFrame:
        rows = []
        for (exp_id, defender), group in self.df.groupby(["experiment_id", "defender"]):
            utility = group["utility_score"].dropna()
            rows.append({
                "experiment_id": exp_id,
                "defender": defender,
                "avg_utility_score": round(utility.mean(), 4) if len(utility) else None,
                "min_utility_score": round(utility.min(), 4) if len(utility) else None,
                "max_utility_score": round(utility.max(), 4) if len(utility) else None,
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 4. Coverage Evaluation (QI 카테고리 / state / transition)
    # ------------------------------------------------------------------
    def compute_coverage_metrics(self) -> Dict[str, Any]:
        """
        로그에 실제로 등장한 QI 카테고리별 turn 수를 센다.
        (QI 전체 목록을 고정해두지 않으므로, '아직 한 번도 안 나온 카테고리'는
        여기서는 계산하지 않는다 - QI 목록 자체가 확정되면 그때 추가해도 됨)
        """
        qi_counter: Dict[str, int] = {}
        for value in self.df["qi"].dropna():
            for q in str(value).split("|"):
                q = q.strip()
                if q:
                    qi_counter[q] = qi_counter.get(q, 0) + 1

        return {
            "qi_category_turn_counts": qi_counter,
            "qi_categories_covered": sorted(qi_counter.keys()),
            "state_count": int(self.df["state_id"].dropna().nunique()) if "state_id" in self.df else 0,
            "transition_count": int(self.df["transition_id"].dropna().nunique()) if "transition_id" in self.df else 0,
        }

    # ------------------------------------------------------------------
    # 5. High-risk Path Corpus (회귀 테스트 / 재공격 replay용)
    #    -> raw QI 값이 아니라 카테고리 이름만 포함
    # ------------------------------------------------------------------
    def extract_high_risk_paths(self, threshold_tau: int) -> pd.DataFrame:
        high_risk = self.df[self.df["k_after"] <= threshold_tau].copy()
        cols = [
            "experiment_id", "target_id", "state_id", "transition_id",
            "turn", "attack_type", "mutation_type", "qi", "k_before", "k_after",
            "defender", "replay_type",
        ]
        cols = [c for c in cols if c in high_risk.columns]
        return high_risk[cols]

    # ------------------------------------------------------------------
    # 6. 종합 리포트 저장 (CSV + Markdown + JSON)
    # ------------------------------------------------------------------
    def export_summary_reports(self, output_dir: str = DEFAULT_RESULT_DIR) -> Dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)

        sec_df = self.compute_security_metrics()
        def_df = self.compute_defender_metrics()
        util_df = self.compute_utility_metrics()

        summary_df = sec_df.merge(def_df, on=["experiment_id", "defender"]) \
                            .merge(util_df, on=["experiment_id", "defender"])

        csv_path = os.path.join(output_dir, "evaluation_summary.csv")
        summary_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        coverage = self.compute_coverage_metrics()
        json_path = os.path.join(output_dir, "coverage_summary.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(coverage, f, ensure_ascii=False, indent=2)

        md_path = os.path.join(output_dir, "evaluation_summary.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Sprint 1 Baseline Evaluation Report\n\n")
            f.write("## Security / Defender / Utility Summary\n\n")
            f.write(summary_df.to_markdown(index=False))
            f.write("\n\n## Coverage\n\n")
            f.write(f"- QI 카테고리 커버리지: {coverage['qi_categories_covered']}\n")
            f.write(f"- State 수: {coverage['state_count']}\n")
            f.write(f"- Transition 수: {coverage['transition_count']}\n")

        return {"csv": csv_path, "md": md_path, "json": json_path}


def main():
    evaluator = BaselineEvaluator(DEFAULT_LOG_FILE)

    if len(evaluator.df) == 0:
        print(
            "\n로그 파일은 있지만 아직 기록된 실험이 0건입니다.\n"
            "Fuzzer/RAG/Defender 쪽에서 LogManager.log_turn()으로 데이터를 "
            "쌓은 뒤 다시 실행해주세요.\n"
            "(파이프라인 자체만 미리 확인하고 싶으면 run_demo.py를 실행하세요.)"
        )
        return

    print("\n" + "=" * 60)
    print(" Privacy Fuzzer - Baseline Evaluation (Sprint 1)")
    print("=" * 60)

    print("\n[Security]")
    print(evaluator.compute_security_metrics().to_string(index=False))

    print("\n[Defender]")
    print(evaluator.compute_defender_metrics().to_string(index=False))

    print("\n[Utility]")
    print(evaluator.compute_utility_metrics().to_string(index=False))

    print("\n[Coverage]")
    print(evaluator.compute_coverage_metrics())

    paths = evaluator.export_summary_reports()
    print("\n결과 저장 완료:")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
