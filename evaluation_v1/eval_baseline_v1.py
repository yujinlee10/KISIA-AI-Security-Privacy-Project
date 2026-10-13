
import json
import os
import pandas as pd
from log_schema import LogManager, VALID_QI

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "logs", "experiment_log.csv")
DEFAULT_RESULT_DIR = os.path.join(BASE_DIR, "results")


class BaselineEvaluator:
#로그 데이터를 분석하여 재현성 검증 및 5대 핵심 지표를 도출하는 평가 클래스.

    def __init__(self, log_csv_path: str = DEFAULT_LOG_FILE):
        self.log_csv_path = log_csv_path
        if not os.path.exists(log_csv_path):
            LogManager(log_csv_path)
        self.df = pd.read_csv(log_csv_path)
        self._preprocess_data()

    def _preprocess_data(self) -> None:
       #데이터프레임 로드 시 숫자형 및 불리언(Boolean) 타입의 일관성 보정
        numeric_cols = ["turn", "k_req", "population_size", "k_run_before", "k_run_after",
                         "k_val", "delta_k", "relative_reduction", "k_floor", "utility_score"]
        for col in numeric_cols:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        bool_cols = ["unlearning", "threshold_violation", "threshold_crossing", "k_decrease",
                     "zero_match", "generalized", "suppressed", "protection_floor_satisfied"]
        for col in bool_cols:
            if col in self.df.columns:
                self.df[col] = (
                    self.df[col].astype(str).str.strip().str.lower()
                    .map({"true": True, "false": False, "1": True, "0": False})
                )

    def check_frozen_params(self) -> pd.DataFrame:
        #파라미터 고정 규칙 검증 (재현성 체크): 동일 실험 내에서 요구 익명성(k_req)이나 모집단 크기(population_size)가 임의로 변동되었는지 확인하여 실험 정합성 위반 여부를 가려냄.
  
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)
        for exp_id, group in self.df.groupby("experiment_id"):
            k_reqs = group["k_req"].dropna().unique() if "k_req" in group else []
            pop_sizes = group["population_size"].dropna().unique() if "population_size" in group else []
            if len(k_reqs) > 1 or len(pop_sizes) > 1:
                rows.append({
                    "experiment_id": exp_id,
                    "k_req_used": list(k_reqs),
                    "population_size_used": list(pop_sizes),
                    "note": "파라미터 고정 규칙 위반 (Frozen Parameter Mismatch)",
                })
        return pd.DataFrame(rows)

    def compute_security_metrics(self) -> pd.DataFrame:
        #보안성 지표 산출 (Security Metrics): 실험 조건별(Condition) 및 방어 기법별(Defender)로 임계치 위반 횟수, 최초 위반 턴, 완전 재식별(Zero-match) 발생 횟수 등을 집계
        
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)
        group_keys = ["experiment_id", "condition", "defender"]
        group_keys = [k for k in group_keys if k in self.df.columns]

        for keys, group in self.df.groupby(group_keys, dropna=False):
            keys = keys if isinstance(keys, tuple) else (keys,)
            violations = int(group["threshold_violation"].fillna(False).sum())
            crossings = int(group["threshold_crossing"].fillna(False).sum())
            zero_matches = int(group["zero_match"].fillna(False).sum())
            crossing_turns = group.loc[group["threshold_crossing"] == True, "turn"]

            row = dict(zip(group_keys, keys))
            row.update({
                "total_turns": len(group),
                "min_k_run_after": group["k_run_after"].min(),
                "threshold_violation_count": violations,
                "threshold_crossing_count": crossings,
                "zero_match_count": zero_matches,
                "first_crossing_turn": int(crossing_turns.min()) if not crossing_turns.empty else None,
            })
            rows.append(row)
        return pd.DataFrame(rows)

    def compute_defender_metrics(self) -> pd.DataFrame:
        #방어 메커니즘 효과성 평가 (Defender Metrics): 일반화(Generalization) 및 억제(Suppression) 적용 비율과 방어 하한선(Protection Floor) 준수 성공률을 산출.
        
        rows = []
        if self.df.empty:
            return pd.DataFrame(rows)
        for (exp_id, condition, defender), group in self.df.groupby(["experiment_id", "condition", "defender"], dropna=False):
            gen_rate = group["generalized"].fillna(False).mean()
            supp_rate = group["suppressed"].fillna(False).mean()
            valid = group.dropna(subset=["k_run_before", "k_run_after", "k_floor"])
            floor_check = valid[(valid["generalized"]) | (valid["suppressed"])]
            floor_success = round(floor_check["protection_floor_satisfied"].mean(), 4) if len(floor_check) > 0 else None

            rows.append({
                "experiment_id": exp_id, "condition": condition, "defender": defender,
                "generalization_rate": round(gen_rate, 4),
                "suppression_rate": round(supp_rate, 4),
                "protection_floor_success_rate": floor_success,
            })
        return pd.DataFrame(rows)

    def compute_coverage_metrics(self) -> dict:
        #준식별자 커버리지 평가 (Coverage Metrics): 8개 VALID_QI 중 실제 실험에서 몇 개나 탐색/활용되었는지 측정.

        if self.df.empty:
            return {"qi_coverage": "0/8", "qi_categories_covered": []}
        seen_qi = set()
        for value in self.df["qi"].dropna():
            for q in str(value).split("|"):
                q = q.strip()
                if q: seen_qi.add(q)
        return {
            "qi_coverage": f"{len(seen_qi)}/{len(VALID_QI)}",
            "qi_categories_covered": sorted(seen_qi),
        }

    def extract_high_risk_paths(self) -> pd.DataFrame:
        """Threshold 위반이 발생한 턴을 고위험 경로 분석용으로 추출한다."""

        return self.df[self.df["threshold_violation"] == True].copy()

    def export_summary_reports(self, output_dir: str = DEFAULT_RESULT_DIR) -> dict[str, str]:
        #산출된 모든 평가 지표를 CSV, JSON, Markdown 형태로 파일 저장
        os.makedirs(output_dir, exist_ok=True)
        sec_df = self.compute_security_metrics()
        def_df = self.compute_defender_metrics()

        csv_path = os.path.join(output_dir, "evaluation_summary.csv")
        sec_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        coverage = self.compute_coverage_metrics()
        json_path = os.path.join(output_dir, "coverage_summary.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(coverage, f, ensure_ascii=False, indent=2)

        md_path = os.path.join(output_dir, "evaluation_summary.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Baseline Evaluation Report (Privacy Framework 개발의사결정 요약 보고서 v1.0 기준)\n\n")
            f.write("## Security Metrics\n\n")
            f.write(sec_df.to_markdown(index=False) if not sec_df.empty else "No logs found.\n")

        return {"csv": csv_path, "json": json_path, "md": md_path}
