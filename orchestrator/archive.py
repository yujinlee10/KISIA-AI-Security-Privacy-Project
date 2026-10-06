# 위험 경로 보관소 (D1 Stage 10: High-risk Path Archive)
#
# 실행 중 k_run이 요구 익명성 아래(1 <= k < k_req)로 내려간 세션을 골라, 그 대화의 질문 순서와 턴별 요약을
# runs/<experiment_id>/high_risk_paths.jsonl 에 한 줄씩 남긴다.
#
# 이 기록이 이어지는 곳
#   python -m validation <실행 폴더>           : 정답과 대조해 진짜 위험이었는지 판정 (validation_status를 정한다)
#   python -m orchestrator.replay --source …   : 보관된 질문을 새 세션에서 그대로 다시 던진다 (Exact Replay)
#   경량 언러닝(C2·C3)                          : 검증된 경로에서 규칙을 만든다 — 아직 구현 전
#
# 보관 시점의 상태는 "실행 중 추정으로 위험해 보인 경로"다 (validation_status = UNVALIDATED).
# 추출 오류로 생긴 가짜 위험일 수 있으므로, 검증 전에는 "위험 경로"라고 단정하지 않는다.
#
# 응답 원문은 여기에 복사하지 않는다. trace.jsonl에서 (target_id, arm, turn)으로 찾는다.
# 질문 원문은 넣는다. 재실행은 질문을 다시 만들지 않고 보관된 문장을 그대로 써야 하기 때문이다 (D1 Stage 13).

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.coverage import state_key
from orchestrator.session import SessionResult

ARCHIVE_FILE_NAME = "high_risk_paths.jsonl"
UNVALIDATED = "UNVALIDATED"


def path_id(experiment_id: str, target_id: str, arm_label: str) -> str:
    return f"{experiment_id}:{target_id}:{arm_label}"


def build_path_record(experiment_id: str, result: SessionResult, k_req: int) -> dict[str, Any]:
    """
    input : experiment_id, result (끝난 세션), k_req
    output : 보관할 기록 (JSON으로 쓸 수 있는 dict)

    first_violation_turn : k_run이 처음 1 <= k < k_req가 된 턴 (경계를 넘은 지점)
    min_k / min_k_turn   : 0명(ZERO_MATCH)을 뺀 최소 k와 그 턴
    queries              : 재실행에 쓸 질문 순서와, 각 질문이 어느 Seed에서 나왔는지
    turns                : 턴별 k, State, 공개 경로
    """
    positive = [(t.commit.k_after, t.turn) for t in result.turns if t.commit.k_after > 0]
    min_k, min_k_turn = min(positive) if positive else (None, None)
    backend = next((t.release.defender_name for t in result.turns), None)

    return {
        "path_id": path_id(experiment_id, result.target_id, result.arm.label),
        "experiment_id": experiment_id,
        "target_id": result.target_id,
        "arm": result.arm.label,
        "backend": backend,
        "k_req": k_req,
        "trigger": "RUNTIME_THRESHOLD_VIOLATION",
        "validation_status": UNVALIDATED,
        "first_violation_turn": result.first_violation_turn(k_req),
        "min_k": min_k,
        "min_k_turn": min_k_turn,
        "zero_match_turns": [t.turn for t in result.turns if t.commit.k_after == 0],
        "k_initial": result.k_initial,
        "queries": [
            {
                "turn": t.turn,
                "query": t.attack.query,
                "seed_id": t.attack.seed_id,
                "seed_version": t.attack.seed_version,
                "objective": t.attack.objective,
                "scenario_code": t.attack.scenario_code,
                "target_qi": t.attack.target_qi,
                "strategy": t.attack.strategy,
                "reset_reason": t.attack.reset_reason,
            }
            for t in result.turns
        ],
        "turns": [
            {
                "turn": t.turn,
                "k_before": t.commit.k_before,
                "k_after": t.commit.k_after,
                "state_key_after": state_key(t.commit.state_after),
                "release_path": t.release.path.value,
                "qi_relation": t.relation,
            }
            for t in result.turns
        ],
    }


def load_paths(run_dir: str | Path) -> list[dict[str, Any]]:
    """
    input : run_dir (runs/<experiment_id>)
    output : 보관된 경로 기록 목록. 보관 파일이 없으면 빈 목록
    """
    path = Path(run_dir) / ARCHIVE_FILE_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
