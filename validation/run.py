# 실행 폴더 하나를 검증한다: trace.jsonl → 턴별 k_val, 세션별 판정, 위험 경로의 검증 상태
#
# 만드는 파일 (실행 폴더 안. 원본 파일은 고치지 않는다)
#   validation.jsonl              : 턴마다 한 줄(k_run, k_val, QI별 판정) + 세션마다 한 줄(요약)
#   experiment_log_validated.csv  : experiment_log.csv에 k_val 컬럼을 채운 사본 (이정민 파트 평가 입력용)
#   validated_paths.jsonl         : high_risk_paths.jsonl에 validation_status를 채운 사본
#
# 위험 경로의 검증 상태
#   VALIDATED_HIGH_RISK : 정답과 맞는 QI만으로 세어도 1 <= k_val < k_req에 도달했다
#   NOT_VALIDATED       : 실행 중에는 기준 아래로 보였지만(k_run), 틀린 QI를 빼면 기준 아래가 아니다
#
# trace는 세션 단위로 읽는다 (orchestrator/trace_reader.py). 같은 experiment_id로 두 번 실행해 기록이
# 이어 붙어 있거나 중간에 멈춘 세션이 있어도 세션을 따로 센다. 그래도 실행마다 experiment_id를 달리하는 편이 안전하다.

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.trace_reader import arm_of, iter_sessions
from validation.validator import StateValidation, Validator, Verdict

TRACE_FILE_NAME = "trace.jsonl"
CSV_FILE_NAME = "experiment_log.csv"
ARCHIVE_FILE_NAME = "high_risk_paths.jsonl"
VALIDATION_FILE_NAME = "validation.jsonl"
VALIDATED_CSV_FILE_NAME = "experiment_log_validated.csv"
VALIDATED_ARCHIVE_FILE_NAME = "validated_paths.jsonl"

VALIDATED_HIGH_RISK = "VALIDATED_HIGH_RISK"
NOT_VALIDATED = "NOT_VALIDATED"


@dataclass(frozen=True)
class SessionValidation:
    """세션 하나의 검증 요약."""
    experiment_id: str
    session_key: str | None
    target_id: str
    arm: str
    turns: int
    k_run_min: int
    k_val_min: int
    runtime_first_violation_turn: int | None
    validated_first_violation_turn: int | None
    incorrect_turns: int             # State에 틀린 QI가 하나라도 있던 턴 수
    incorrect_qis: tuple[str, ...]   # 세션 중 한 번이라도 틀린 값이 들어갔던 QI
    archived_path_id: str | None
    finished: bool = True            # False면 session_end 없이 끊긴 세션 (중간에 멈춘 실행)

    @property
    def validated_high_risk(self) -> bool:
        return self.validated_first_violation_turn is not None

    def to_log_dict(self) -> dict[str, Any]:
        return {"type": "session", **vars(self), "incorrect_qis": list(self.incorrect_qis),
                "validated_high_risk": self.validated_high_risk}


def validate_run(run_dir: str | Path, validator: Validator, k_req: int) -> list[SessionValidation]:
    """
    input : run_dir (runs/<experiment_id>), validator (그 실행의 백엔드에 맞는 검증기), k_req
    output : 세션별 검증 요약 (파일도 함께 쓴다). 중간에 멈춘 세션도 포함한다 (finished = False)
    """
    run_dir = Path(run_dir)
    trace_path = run_dir / TRACE_FILE_NAME
    if not trace_path.exists():
        raise FileNotFoundError(f"trace 파일이 없습니다: {trace_path}")

    lines: list[dict[str, Any]] = []
    sessions: list[SessionValidation] = []
    k_val_by_turn: list[int] = []          # trace의 turn 줄 순서 = CSV 행 순서

    for session in iter_sessions(trace_path):
        checked = [(item, validator.validate(item["target_id"], item["commit"]["state_after"]))
                   for item in session.turns]
        for item, result in checked:
            k_val_by_turn.append(result.k_val)
            lines.append({
                "type": "turn",
                "experiment_id": item["experiment_id"],
                "session_key": item.get("session_key"),
                "target_id": item["target_id"],
                "arm": arm_of(item),
                "turn": item["turn"],
                "k_run": item["commit"]["k_after"],
                "k_val": result.k_val,
                "verdicts": {qi: verdict.value for qi, verdict in result.verdicts.items()},
                "target_in_run_candidates": result.target_in_run_candidates,
                "validated_violation": 1 <= result.k_val < k_req,
            })
        if checked:
            summary = _summarise(session.end, checked, k_req)
            sessions.append(summary)
            lines.append(summary.to_log_dict())

    validated_rows = _validated_csv_rows(run_dir, k_val_by_turn)      # 행 수가 안 맞으면 아무것도 쓰기 전에 멈춘다
    with (run_dir / VALIDATION_FILE_NAME).open("w", encoding="utf-8") as file:
        for line in lines:
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
    if validated_rows is not None:
        fieldnames, rows = validated_rows
        with (run_dir / VALIDATED_CSV_FILE_NAME).open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    _write_validated_archive(run_dir, sessions)
    return sessions


def _summarise(end: dict[str, Any] | None, turns: list[tuple[dict[str, Any], StateValidation]],
               k_req: int) -> SessionValidation:
    first = turns[0][0]
    runtime_first = next((item["turn"] for item, _ in turns if 1 <= item["commit"]["k_after"] < k_req), None)
    validated_first = next((item["turn"] for item, result in turns if 1 <= result.k_val < k_req), None)
    incorrect: list[str] = []
    for _, result in turns:
        incorrect.extend(qi for qi in result.incorrect_qis if qi not in incorrect)
    return SessionValidation(
        experiment_id=first["experiment_id"],
        session_key=first.get("session_key"),
        target_id=first["target_id"],
        arm=arm_of(first),
        turns=len(turns),
        k_run_min=min(item["commit"]["k_after"] for item, _ in turns),
        k_val_min=min(result.k_val for _, result in turns),
        runtime_first_violation_turn=runtime_first,
        validated_first_violation_turn=validated_first,
        incorrect_turns=sum(1 for _, result in turns if result.incorrect_qis),
        incorrect_qis=tuple(incorrect),
        archived_path_id=(end or {}).get("archived_path_id"),
        finished=end is not None,
    )


def _validated_csv_rows(run_dir: Path, k_val_by_turn: list[int]) -> tuple[list[str], list[dict[str, Any]]] | None:
    source = run_dir / CSV_FILE_NAME
    if not source.exists():
        return None
    with source.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames, rows = list(reader.fieldnames or []), list(reader)
    if len(rows) != len(k_val_by_turn):
        raise ValueError(f"{source.name}의 행 수({len(rows)})와 trace의 턴 수({len(k_val_by_turn)})가 다릅니다. "
                         "두 파일이 같은 실행에서 나온 것인지 확인하세요.")
    for row, k_val in zip(rows, k_val_by_turn):
        row["k_val"] = k_val
    return fieldnames, rows


def _write_validated_archive(run_dir: Path, sessions: list[SessionValidation]) -> None:
    """
    보관 기록과 세션을 path_id로 잇는다. 같은 experiment_id로 다시 실행해 같은 path_id가 여러 번 있으면
    적힌 순서대로 하나씩 짝짓는다 (보관소와 trace 모두 실행 순서대로 이어 쓰기 때문).
    """
    source = run_dir / ARCHIVE_FILE_NAME
    if not source.exists():
        return
    waiting: dict[str, list[SessionValidation]] = {}
    for session in sessions:
        if session.archived_path_id:
            waiting.setdefault(session.archived_path_id, []).append(session)
    with (run_dir / VALIDATED_ARCHIVE_FILE_NAME).open("w", encoding="utf-8") as file:
        for raw in source.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            queue = waiting.get(record["path_id"])
            session = queue.pop(0) if queue else None
            if session is not None:
                record["validation_status"] = VALIDATED_HIGH_RISK if session.validated_high_risk else NOT_VALIDATED
                record["k_val_min"] = session.k_val_min
                record["validated_first_violation_turn"] = session.validated_first_violation_turn
                record["incorrect_qis"] = list(session.incorrect_qis)
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


__all__ = ["SessionValidation", "validate_run", "Verdict", "VALIDATED_HIGH_RISK", "NOT_VALIDATED"]
