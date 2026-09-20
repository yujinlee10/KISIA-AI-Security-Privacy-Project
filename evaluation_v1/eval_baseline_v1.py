
import json
import os
import pandas as pd

from log_schema_v1 import LogManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "logs", "experiment_log.csv")
DEFAULT_RESULT_DIR = os.path.join(BASE_DIR, "results")


class BaselineEvaluator:
    """로그 분석 및 평가 클래스"""

    def __init__(self, log_csv_path: str = DEFAULT_LOG_FILE):
        #LogManger(log_csv_path)를 직접 호출해 디렉토리와 헤더 갖춘 빈 CSV 파일이 생성
        self.log_csv_path = log_csv_path
        if not os.path.exists(log_csv_path):
            # 아직 아무도 로그를 안 남겼어도 에러 없이 빈 로그 파일을 만들고 진행.
            print(f"[안내] 지정한 로그 파일이 없어 신규 생성합니다: {log_csv_path}")
            LogManager(log_csv_path)

        self.df = pd.read_csv(log_csv_path)
        self._preprocess_data()

    def _preprocess_data(self) -> None:
        """데이터 타입 전처리 (숫자, 부울, 날짜 타입 정리)"""
        numeric_cols = ["turn", "k_before", "k_after", "threshold", "utility_score"]
        for col in numeric_cols:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        bool_cols = ["risk_event", "generalized", "suppressed"]
        for col in bool_cols:
            if col in self.df.columns:
                self.df[col] = (
                    self.df[col]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .map({"true": True, "false": False, "1": True, "0": False})
                )

    # 1. 보안 지표 (Security Metrics) : 최소 k값, Risk Event 발생 빈도, 위험 도달 턴 수 등)
    def compute_security_metrics(self) -> pd.DataFrame:
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)

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

    # 2. 방어 지표 (Defender Metrics)방어 성능 지표: Generalization/Supperesion 비율 및 위험 완화 성공률
    # 원래 위험했던 turn (k_before <= threshold)' 중 방어 후 안전해진 turn (k_after > threshold)의 비율.
    
    def compute_defender_metrics(self) -> pd.DataFrame:
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)

        for (exp_id, defender), group in self.df.groupby(["experiment_id", "defender"]):
            gen_rate = group["generalized"].fillna(False).mean()
            supp_rate = group["suppressed"].fillna(False).mean()

            valid = group.dropna(subset=["k_before", "k_after", "threshold"])
            if len(valid) > 0:
                risk_before = valid["k_before"] <= valid["threshold"]
                safe_after = valid["k_after"] > valid["threshold"]
                success_rate = (
                    round((safe_after & risk_before).sum() / risk_before.sum(), 4)
                    if risk_before.sum() > 0
                    else None
                )
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

    # 3. 유용성 지표 (Utility Metrics) : 방어 기법 적용 시 응답의 평균/최소/최대 Utility 점수)
    def compute_utility_metrics(self) -> pd.DataFrame:
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)

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

    # 4. 커버리지 지표 (Coverage Metrics) : coverage 지표: 노출된 QI 카테고리 수, Unique State 수, Transition 수
    # 로그에 실제로 등장한 QI 카테고리별 turn 수를 셈. 한 번도 안 나온 카테고리는 계산하지 않음.
    def compute_coverage_metrics(self) -> dict:
        qi_counter: dict[str, int] = {}
        if self.df.empty:
            return {
                "qi_category_turn_counts": qi_counter,
                "qi_categories_covered": [],
                "state_count": 0,
                "transition_count": 0,
            }

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

    # 5. High-risk Path Corpus (회귀 테스트 / 재공격 replay용) -> raw QI 값이 아니라 카테고리 이름만 포함
    def extract_high_risk_paths(self, threshold_tau: int) -> pd.DataFrame:
        # k_after가 설정한 임계값(tau) 이하인 고위험 레코드만 추출
        high_risk = self.df[self.df["k_after"] <= threshold_tau].copy()
    
        # Replay 및 재검증 분석에 필요한 주요 필드 선택
        cols = [
            "experiment_id", "target_id", "state_id", "transition_id",
            "turn", "attack_type", "mutation_type", "qi", "k_before", "k_after",
            "defender", "replay_type",
        ]
        cols = [c for c in cols if c in high_risk.columns]
        return high_risk[cols]
    


    # 리포트 내보내기 (CSV, JSON, Markdown)
    def export_summary_reports(self, output_dir: str = DEFAULT_RESULT_DIR) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)

        sec_df = self.compute_security_metrics()
        def_df = self.compute_defender_metrics()
        util_df = self.compute_utility_metrics()

        summary_df = sec_df
        for item in [def_df, util_df]:
            if not item.empty:
                summary_df = summary_df.merge(item, on=["experiment_id", "defender"], how="left")

        # CSV 저장
        csv_path = os.path.join(output_dir, "evaluation_summary.csv")
        summary_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        # JSON 저장
        coverage = self.compute_coverage_metrics()
        json_path = os.path.join(output_dir, "coverage_summary.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(coverage, f, ensure_ascii=False, indent=2)

        # Markdown 저장
        md_path = os.path.join(output_dir, "evaluation_summary.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Baseline Evaluation Report\n\n")
            f.write("## Integrated Evaluation Summary\n\n")
            if not summary_df.empty:
                f.write(summary_df.to_markdown(index=False))
            else:
                f.write("No experiment logs found.\n")
            f.write("\n\n## Coverage Summary\n\n")
            f.write(f"- Covered QI Categories: {coverage['qi_categories_covered']}\n")
            f.write(f"- Total Unique States: {coverage['state_count']}\n")
            f.write(f"- Total Unique Transitions: {coverage['transition_count']}\n")

        return {"csv": csv_path, "md": md_path, "json": json_path}


if __name__ == "__main__":
    evaluator = BaselineEvaluator()
    results = evaluator.export_summary_reports()
    print("[성공] 평가 리포트 내보내기 완료:", results)