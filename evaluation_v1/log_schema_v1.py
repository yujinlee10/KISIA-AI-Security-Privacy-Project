
import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# 표준 20개 CSV 로그 컬럼 정의
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
    """단일 대화 턴(Turn)에서의 실험 로그 데이터 클래스"""
 # --- 필수 필드 ---
    experiment_id: str          # 실험 식별자
    target_id: str              # Target 페르소나의 person_id (내부 식별자, username 아님)
    defender: str               # 방어 기법 (예: "none", "ontology_gen_v1", "suppress")
    turn: int                   # 대화 턴 번호 (1부터 시작)
    attack_type: str            # 공격 유형 (예: "direct", "indirect_reframing")
    mutation_type: str          # 변형 기법 (예: "none", "specificity_increase", "paraphrase")
    qi: List[str]               # 감지된 QI 카테고리 리스트 (값은 저장하지 않음.)
    k_before: int                # 이번 turn 처리 전 후보군 크기 k
    k_after: int                 # 이번 turn 처리 후 후보군 크기k
    risk_event: bool             # 위험 이벤트 발생 여부 (k_after <= threshold)
    generalized: bool            # Defender의 일반화 적용 여부
    suppressed: bool             # Defender의 억제/거부 (Response Suppression) 적용 여부

    # --- 선택 필드 ---
    utility_score: Optional[float] = None   # 응답 유용성 점수 (0.0 ~ 1.0)
    transition_id: Optional[str] = None     # Fuzzer 상태 전이 ID
    state_id: Optional[str] = None          # Privacy State ID
    threshold: Optional[int] = 3          # k-anonymity 임계값 tau (기본값 3)
    model: Optional[str] = None              # 사용된 LLM 모델명 (예: "gpt-4o-mini")
    config: Optional[str] = "temp0.0"             # 설정 파라미터 (예: "temp0.0_top_p1.0")
    replay_type: Optional[str] = "initial"        # "initial" | "exact_replay" | "mutated_replay"
    timestamp: str = field(
        default_factory=lambda: datetime.now(KST).isoformat() #한국 표준시 기준 ISO 스탬프 기록
    )

    def __post_init__(self):
        #qi 파라미터 방어적 정제 (어떤 타입이 들어와도 List[str]로 변환)
        if self.qi is None:
            self.qi = []
        elif isinstance(self.qi, str):
            # "age, location" 또는 "age|location" 형태로 들어온 경우
            self.qi = [
                q.strip()
                for q in self.qi.replace(",", "|").split("|")
                if q.strip()
            ]
        elif isinstance(self.qi, (list, tuple, set)):
            # 리스트/튜플 안에 객체가 있을 경우 str(q)로 변환
            self.qi = [
                str(q.name) if hasattr(q, "name") else str(q) for q in self.qi
            ]
        else:
            self.qi = [str(self.qi)]

        """k_after와 risk_event 간의 논리 정합성 검증"""
        if self.threshold is not None and self.k_after is not None:
            expected_risk = self.k_after <= self.threshold
            if bool(self.risk_event) != expected_risk:
                print(
                    f"[LOG WARNING] risk_event 불일치! "
                    f"(risk_event={self.risk_event}, k_after={self.k_after}, threshold={self.threshold})"
                )

    def to_row(self) -> dict:
        """CSV 딕셔너리 매핑 변환"""
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
    """CSV 로그 파일 작성 및 관리 클래스"""

    def __init__(self, csv_filepath: str):
        self.csv_filepath = csv_filepath
        self._init_csv()

    def _init_csv(self) -> None:
        """파일 존재 여부 확인 후 헤더 작성"""
        dirname = os.path.dirname(self.csv_filepath)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        if not os.path.exists(self.csv_filepath):
            with open(self.csv_filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
                writer.writeheader()

    def log_turn(self, record: ExperimentLogRecord) -> None:
        """단일 턴 기록을 CSV에 한 줄씩 작성"""
        with open(self.csv_filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            writer.writerow(record.to_row())