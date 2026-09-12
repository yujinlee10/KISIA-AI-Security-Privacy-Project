from dataclasses import dataclass, field
from typing import List, Optional
import csv
import os
from datetime import datetime, timezone, timedelta

# 필수 컬럼 + 확장 컬럼(transition_id, state_id, threshold, model, config, replay_type, timestamp)
LOG_COLUMNS = [
    "experiment_id",
    "target_id",
    "defender",
    "turn",
    "attack_type",
    "mutation_type",
    "qi",
    "k_before",
    "k_after",
    "risk_event",
    "generalized",
    "suppressed",
    "utility_score",
    "transition_id",
    "state_id",
    "threshold",
    "model",
    "config",
    "replay_type",
    "timestamp",
]

# KST 시차 (+9시간) 정의
KST = timezone(timedelta(hours=9))

@dataclass
class ExperimentLogRecord:
    """실험 로그 단일 레코드 (한 turn = 한 레코드)."""

    # --- 필수 필드 ---
    experiment_id: str          # 예: "exp_sprint1_001"
    target_id: str              # Target 페르소나의 person_id (내부 식별자, username 아님)
    defender: str               # 예: "none", "ontology_gen_v1", "suppress"
    turn: int                   # 1부터 시작
    attack_type: str            # 예: "direct", "indirect_reframing"
    mutation_type: str          # 예: "none", "specificity_increase", "paraphrase"
    qi: List[str]               # 이번 turn에 새로 감지된 QI '카테고리' 이름만. 값은 저장하지 않음.
    k_before: int                # 이번 turn 처리 전 Reference Population k
    k_after: int                 # 이번 turn 처리 후 k
    risk_event: bool             # k_after <= threshold 여부
    generalized: bool            # Defender의 일반화 적용 여부
    suppressed: bool             # Defender의 억제/거부 적용 여부

    # --- 선택 필드 ---
    utility_score: Optional[float] = None   # 0.0 ~ 1.0
    transition_id: Optional[str] = None
    state_id: Optional[str] = None
    threshold: Optional[int] = None          # k-anonymity 임계값 tau
    model: Optional[str] = None              # 예: "gpt-4o-mini"
    config: Optional[str] = None             # 예: "temp0.0_top_p1.0"
    replay_type: Optional[str] = None        # "initial" | "exact_replay" | "mutated_replay"
    timestamp: str = field(
        #default_factory=lambda: datetime.now(timezone.utc).isoformat()
        default_factory=lambda: datetime.now(KST).isoformat()
    )  # 레코드 생성 시각(UTC), 따로 안 넘기면 자동으로 채워짐

    def __post_init__(self):
        if self.threshold is not None and self.k_after is not None:
            expected = self.k_after <= self.threshold
            if bool(self.risk_event) != expected:
                print(
                    f"[경고] risk_event({self.risk_event})가 k_after<=threshold 판정"
                    f"({expected})과 다릅니다. (experiment_id={self.experiment_id}, turn={self.turn})"
                )

    def to_row(self) -> dict:
        """CSV 저장을 위한 dict 변환. qi 리스트는 '|'로 이어붙인 문자열로 직렬화."""
        return {
            "experiment_id": self.experiment_id,
            "target_id": self.target_id,
            "defender": self.defender,
            "turn": self.turn,
            "attack_type": self.attack_type,
            "mutation_type": self.mutation_type,
            "qi": "|".join(self.qi),
            "k_before": self.k_before,
            "k_after": self.k_after,
            "risk_event": bool(self.risk_event),
            "generalized": bool(self.generalized),
            "suppressed": bool(self.suppressed),
            "utility_score": self.utility_score,
            "transition_id": self.transition_id,
            "state_id": self.state_id,
            "threshold": self.threshold,
            "model": self.model,
            "config": self.config,
            "replay_type": self.replay_type,
            "timestamp": self.timestamp,
        }


class LogManager:
    """실험 로그를 CSV로 append 기록하는 관리자."""

    def __init__(self, csv_filepath: str):
        self.csv_filepath = csv_filepath
        self._init_csv()

    def _init_csv(self) -> None:
        dirname = os.path.dirname(self.csv_filepath)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        if not os.path.exists(self.csv_filepath):
            with open(self.csv_filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
                writer.writeheader()

    def log_turn(self, record: ExperimentLogRecord) -> None:
        with open(self.csv_filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            writer.writerow(record.to_row())
