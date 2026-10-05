# 실행 기록: 한 run(experiment_id)의 결과를 runs/<experiment_id>/ 아래 세 파일로 남긴다
#
#   experiment_log.csv : 이정민 파트 evaluation_v1/log_schema_v1.py 형식 그대로 (턴마다 한 줄)
#                        ExperimentLogRecord·LogManager를 수정 없이 불러 쓴다. 지표 계산도 그쪽 __post_init__이 한다.
#   trace.jsonl        : trusted 로그 (턴마다 한 줄). raw candidate, 추출·정규화·Defender·재작성 내역 전부.
#                        공격자 쪽 모듈(Fuzzer, Mutation LLM)은 이 파일을 읽지 않는다.
#   run_config.json    : 어떤 설정으로 돌렸는지 (모델, prompt 버전, 기준 집단, Seed 파일 해시, 판정 기준 등)
#
# runs/는 .gitignore 대상이다. trace에는 대상 persona의 응답 원문이 들어 있어 저장소에 올리지 않는다.
# API 키는 어느 파일에도 쓰지 않는다 (run_config에는 core Settings.describe() 결과만 들어간다).

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from adapters.risk_adapter import RiskConfig
from evaluation_v1.log_schema_v1 import KST, ExperimentLogRecord, LogManager
from orchestrator.release_gate import Arm, DefenderMode
from orchestrator.session import SessionResult, TurnRecord

CSV_FILE_NAME = "experiment_log.csv"
TRACE_FILE_NAME = "trace.jsonl"
CONFIG_FILE_NAME = "run_config.json"

NO_DEFENDER_NAME = "none"         # evaluation 로그 defender 컬럼 값 (C0)
STATELESS_SUFFIX = "_stateless"   # 무상태 Defender는 백엔드 이름 뒤에 붙인다 (예: risk_defender_stateless)


class RunLogger:
    def __init__(self, run_dir: Path, *, experiment_id: str, risk_config: RiskConfig,
                 population_size: int, model: str | None, generation_config: str) -> None:
        """
        input : run_dir (runs/<experiment_id>), experiment_id, risk_config (k_req 기록용),
                population_size, model (LLM 모델 ID), generation_config (prompt 버전 등 짧은 설명)
        output : 없음

        같은 experiment_id로 다시 돌리면 CSV·trace 뒤에 이어 쓴다. 섞이지 않게 하려면 experiment_id를 바꾼다.
        """
        self._run_dir = Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._experiment_id = experiment_id
        self._risk_config = risk_config
        self._population_size = population_size
        self._model = model
        self._generation_config = generation_config
        self._csv = LogManager(str(self.csv_path))

    @property
    def csv_path(self) -> Path:
        return self._run_dir / CSV_FILE_NAME

    @property
    def trace_path(self) -> Path:
        return self._run_dir / TRACE_FILE_NAME

    @property
    def config_path(self) -> Path:
        return self._run_dir / CONFIG_FILE_NAME

    # ── run 설정 ─────────────────────────────────────────

    def write_config(self, config: dict[str, Any]) -> None:
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 턴 기록 ──────────────────────────────────────────

    def log_turn(self, target_id: str, arm: Arm, record: TurnRecord) -> ExperimentLogRecord:
        """
        input : target_id, arm (실험 갈래), record (세션이 만든 턴 기록)
        output : evaluation 형식 레코드 (CSV에 쓴 것과 같은 객체)
        """
        row = self.to_evaluation_record(target_id, arm, record)
        self._csv.log_turn(row)
        self._append_trace({
            "type": "turn",
            "experiment_id": self._experiment_id,
            "target_id": target_id,
            "arm": arm.label,
            "condition": arm.condition.value,
            **record.to_trace_dict(),
        })
        return row

    def log_session_end(self, result: SessionResult) -> None:
        self._append_trace({
            "type": "session_end",
            "experiment_id": self._experiment_id,
            "target_id": result.target_id,
            "arm": result.arm.label,
            "condition": result.condition.value,
            "turns": len(result.turns),
            "stop_reason": result.stop_reason.value,
            "stop_detail": result.stop_detail,
            "k_initial": result.k_initial,
            "k_final": result.k_final,
            "k_min": result.k_min,
            "first_violation_turn": result.first_violation_turn(self._risk_config.k_req),
            "unresolved_released": result.unresolved_released,
            "coverage": result.coverage,
        })

    def to_evaluation_record(self, target_id: str, arm: Arm,
                             record: TurnRecord) -> ExperimentLogRecord:
        """
        TurnRecord → 이정민 파트 ExperimentLogRecord.

        넘기지 않는 값
          operator_id : evaluation은 M1~M6을 기대하는데 한재이 파트 전략은 intensify·refine 등이라 대응표가 없다.
                        전략 이름은 trace의 attack.strategy에 남긴다.
          k_val       : 정답(GT) 대조 후 계산하는 값이라 실행 중에는 만들지 않는다 (D1: Runtime에 GT 사용 금지).
          utility_score : 유용성 채점 기준이 정해진 뒤 채운다.
        """
        attack, commit, release = record.attack, record.commit, record.release
        defender = NO_DEFENDER_NAME
        if release.defender_enabled:
            stateless = release.defender_mode is DefenderMode.STATELESS
            defender = release.defender_name + (STATELESS_SUFFIX if stateless else "")
        return ExperimentLogRecord(
            experiment_id=self._experiment_id,
            target_id=target_id,
            turn=record.turn,
            seed_id=attack.seed_id,
            objective=attack.objective,
            scenario_id=attack.scenario_code,
            target_qi=attack.target_qi,
            k_run_before=commit.k_before,
            k_run_after=commit.k_after,
            condition=arm.condition.value,
            defender=defender,
            unlearning=False,
            qi_relation=record.relation,
            qi=list(record.disclosed_after),
            k_req=self._risk_config.k_req,
            population_size=self._population_size,
            generalized=release.generalized,
            suppressed=release.suppressed,
            k_floor=release.k_floor,
            state_id=record.state_id_after,
            transition_id=f"{record.state_id_before}>{record.state_id_after}",
            model=self._model,
            config=self._generation_config,
            replay_type="initial",
        )

    def _append_trace(self, item: dict[str, Any]) -> None:
        item = {"logged_at": datetime.now(KST).isoformat(), **item}
        with self.trace_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
