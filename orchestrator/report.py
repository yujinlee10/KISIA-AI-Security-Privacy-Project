# 실행 결과 집계: python -m orchestrator.report <experiment_id> [<experiment_id> …]
#
# runs/<experiment_id>/trace.jsonl을 읽어 세션별·갈래별 표를 만든다. LLM도 기준 집단도 쓰지 않는다.
# 보고서의 숫자를 눈으로 세지 않도록, 집계는 이 명령 하나로 다시 만들 수 있게 한다.
#
# 출력
#   화면과 runs/<experiment_id>/report.md (같은 내용, markdown 표)
#   validation.jsonl이 있으면 (python -m validation을 먼저 돌렸으면) k_val 열이 채워진다. 없으면 "-"로 둔다.
#
# 표의 숫자를 읽을 때
#   k는 실행 중 추정값(k_run)이다. 정답과 대조한 값은 k_val 열이다.
#   "미해석"은 공개된 응답에서 추출됐지만 정규화하지 못해 k에 반영되지 않은 관찰이다.
#   이 수가 0이 아니면 그 세션의 k는 실제보다 크다.
#   trace는 세션 단위로 읽는다. 같은 experiment_id로 두 번 돌려 기록이 이어 붙었으면 세션이 두 번씩 나온다.
#
# report.md에는 정규화하지 못한 값(응답에서 나온 짧은 구절)이 들어간다. runs/ 안에만 두고 커밋하지 않는다.

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from core.config import PROJECT_ROOT
from orchestrator.trace_reader import arm_of, iter_sessions, read_jsonl
from orchestrator.utility import TurnUtility, mean, similarity_floor, turn_utility

DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
TRACE_FILE_NAME = "trace.jsonl"
CONFIG_FILE_NAME = "run_config.json"
VALIDATION_FILE_NAME = "validation.jsonl"
REPORT_FILE_NAME = "report.md"

RELEASE_PATHS = ("PASSTHROUGH", "ALLOWED", "REWRITTEN", "REFUSAL")
ROLES = ("mutation", "target", "extractor", "rewriter")
MAX_UNRESOLVED_ROWS = 40


@dataclass
class SessionStats:
    """세션 하나의 집계 (trace의 turn 줄들과 session_end 줄 하나에서 만든다)."""
    target_id: str
    arm: str
    session_key: str | None
    turns: int = 0
    stop_reason: str | None = None
    k_initial: int | None = None
    k_min: int | None = None
    k_final: int | None = None
    first_violation_turn: int | None = None
    unresolved_released: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)
    zero_match_turns: int = 0
    paths: Counter = field(default_factory=Counter)
    refusal_reasons: Counter = field(default_factory=Counter)
    generalized_turns: int = 0
    suppressed_turns: int = 0
    second_round_turns: int = 0
    adopted: int = 0
    extraction_failures: int = 0
    rejected_observations: int = 0
    statuses: Counter = field(default_factory=Counter)             # 원 응답 관찰의 정규화 결과
    committed_by_qi: Counter = field(default_factory=Counter)
    unresolved: list[dict[str, Any]] = field(default_factory=list)  # 공개분 중 정규화하지 못한 관찰
    seeds: list[str] = field(default_factory=list)
    target_qis: Counter = field(default_factory=Counter)
    reset_reasons: Counter = field(default_factory=Counter)
    rejected_mutations: Counter = field(default_factory=Counter)    # 버린 변형 결과 (사유별)
    questions_from: str | None = None                               # 다른 갈래의 질문을 그대로 쓴 세션이면 그 갈래
    intervention_reasons: Counter = field(default_factory=Counter)  # Defender가 개입한 이유 (회차별)
    residual_rounds: int = 0                                        # 지운 값의 근거 문구가 남아 다시 고친 회차 수
    utilities: list[tuple[str, TurnUtility, int]] = field(default_factory=list)   # (공개 경로, 유용성, 원 응답의 QI 수)
    raw_responses: list[str] = field(default_factory=list)          # 바닥값 계산용 (집계 뒤 버린다)
    fallbacks: int = 0
    tokens: dict[str, list[int]] = field(default_factory=lambda: {role: [0, 0] for role in ROLES})
    calls: Counter = field(default_factory=Counter)
    k_val_min: int | None = None
    validated_first_violation_turn: int | None = None
    incorrect_qis: tuple[str, ...] = ()
    validated: bool = False

    def add_turn(self, item: dict[str, Any]) -> None:
        self.turns += 1
        attack, target, release, commit = item["attack"], item["target"], item["release"], item["commit"]

        if attack["seed_id"] not in self.seeds:
            self.seeds.append(attack["seed_id"])
        self.target_qis[attack["target_qi"]] += 1
        if attack.get("reset_reason"):
            self.reset_reasons[attack["reset_reason"]] += 1
        self.fallbacks += bool(attack.get("fallback_used"))
        self.rejected_mutations.update(attack.get("invalid_reasons") or [])
        self.questions_from = item.get("questions_from") or self.questions_from
        self._spend("mutation", attack.get("mutation_input_tokens"), attack.get("mutation_output_tokens"),
                    attack.get("mutation_attempts"))
        self._spend("target", target.get("input_tokens"), target.get("output_tokens"), target.get("attempts"))

        extraction = item.get("raw_extraction")
        if item.get("raw_extraction_error") or extraction is None:
            self.extraction_failures += 1
        if extraction:
            self.rejected_observations += len(extraction.get("rejected") or [])
            self._spend("extractor", extraction.get("input_tokens"), extraction.get("output_tokens"),
                        extraction.get("attempts"))
        for observation in item.get("raw_normalized") or []:
            self.statuses[observation["status"]] += 1

        self.paths[release["path"]] += 1
        raw_text = target.get("raw_candidate_response") or ""
        raw_attributes = [obs["attribute"] for obs in item.get("raw_normalized") or [] if obs["status"] == "MAPPED"]
        self.utilities.append((release["path"], turn_utility(
            raw_text, release.get("released_response") or "", raw_attributes,
            [obs["attribute"] for obs in release.get("released_observations") or [] if obs["status"] == "MAPPED"]),
            len(set(raw_attributes))))
        self.raw_responses.append(raw_text)
        if release.get("refusal_reason"):
            self.refusal_reasons[release["refusal_reason"]] += 1
        self.generalized_turns += bool(release.get("generalized"))
        self.suppressed_turns += bool(release.get("suppressed"))
        rounds = release.get("rounds") or []
        self.second_round_turns += len(rounds) >= 2
        for round_item in rounds:
            self.adopted += len(round_item.get("adopted_ids") or [])
            self.residual_rounds += bool(round_item.get("residual_evidence"))
            defense = round_item.get("defense") or {}
            if defense.get("intervened"):
                logs = defense.get("defense_log") or [{}]
                reason = logs[0].get("intervention_reason") if isinstance(logs[0], dict) else None
                self.intervention_reasons[reason or defense.get("action") or "?"] += 1
            rewrite = round_item.get("rewrite")
            if rewrite:
                self._spend("rewriter", rewrite.get("input_tokens"), rewrite.get("output_tokens"), 1)
                self._spend("extractor", round_item.get("extraction_input_tokens"),
                            round_item.get("extraction_output_tokens"), 1)

        committed = set(commit.get("committed_ids") or [])
        for observation in release.get("released_observations") or []:
            if observation["observation_id"] in committed:
                self.committed_by_qi[observation["attribute"]] += 1
            if observation["status"] != "MAPPED":
                self.unresolved.append({"turn": item["turn"], **{key: observation.get(key) for key in
                                        ("attribute", "raw_value", "value_en", "status", "reason")}})
        self.zero_match_turns += commit["k_after"] == 0

    def _spend(self, role: str, tokens_in: Any, tokens_out: Any, calls: Any) -> None:
        self.tokens[role][0] += int(tokens_in or 0)
        self.tokens[role][1] += int(tokens_out or 0)
        self.calls[role] += int(calls or 0)

    def close(self, end: dict[str, Any]) -> None:
        for name in ("stop_reason", "k_initial", "k_min", "k_final", "first_violation_turn", "unresolved_released"):
            setattr(self, name, end.get(name))
        self.unresolved_released = self.unresolved_released or 0
        self.coverage = end.get("coverage") or {}


@dataclass
class RunReport:
    experiment_id: str
    config: dict[str, Any]
    sessions: list[SessionStats]
    unfinished_turns: int          # session_end 없이 끝난 턴 수 (중간에 멈춘 실행)
    validated: bool
    superseded: int = 0            # 같은 대상·갈래를 다시 돌려 앞의 것이 집계에서 빠진 세션 수
    similarity_floor: float | None = None   # 서로 다른 질문에 대한 원 응답끼리의 평균 유사도 (유용성 바닥값)
    strata: dict[str, str] = field(default_factory=dict)   # 대상 → 층 (대상 선정 파일을 주었을 때)


def load_run(run_dir: str | Path, strata: dict[str, str] | None = None) -> RunReport:
    """
    input : run_dir (runs/<experiment_id>)
    input(추가) : strata (대상 → 층. 대상 선정 파일에서 읽은 것. 주면 층별 표를 만든다)
    output : RunReport (세션별 집계. validation.jsonl이 있으면 k_val도 붙인다)
    """
    run_dir = Path(run_dir)
    trace_path = run_dir / TRACE_FILE_NAME
    if not trace_path.exists():
        raise FileNotFoundError(f"trace 파일이 없습니다: {trace_path}")
    config_path = run_dir / CONFIG_FILE_NAME
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    sessions: list[SessionStats] = []
    unfinished = 0
    for session in iter_sessions(trace_path):
        if not session.finished:         # session_end 없이 끊긴 세션은 표에 넣지 않고 턴 수만 알린다
            unfinished += len(session.turns)
            continue
        stats = SessionStats(session.head["target_id"], arm_of(session.head), session.head.get("session_key"))
        for item in session.turns:
            stats.add_turn(item)
        stats.close(session.end)
        sessions.append(stats)

    validation_path = run_dir / VALIDATION_FILE_NAME
    validated = validation_path.exists()
    if validated:
        summaries = [item for item in read_jsonl(validation_path)
                     if item.get("type") == "session" and item.get("finished", True)]
        finished = [session for session in sessions if session.turns]
        if len(summaries) == len(finished):          # 같은 trace에서 만든 것일 때만 붙인다 (순서가 같다)
            for session, summary in zip(finished, summaries):
                session.k_val_min = summary["k_val_min"]
                session.validated_first_violation_turn = summary["validated_first_violation_turn"]
                session.incorrect_qis = tuple(summary["incorrect_qis"])
                session.validated = True
        else:
            validated = False

    # 같은 대상·갈래가 여러 번 끝났으면 (멈춘 실행을 --resume으로 이어 돌린 경우 등) 마지막 것만 집계한다
    latest: dict[tuple[Any, ...], SessionStats] = {}
    for stats in sessions:
        latest[(stats.target_id, stats.arm, stats.session_key)] = stats
    kept = [stats for stats in sessions if latest[(stats.target_id, stats.arm, stats.session_key)] is stats]
    floor = similarity_floor([text for stats in kept if stats.questions_from is None for text in stats.raw_responses])
    for stats in sessions:
        stats.raw_responses = []
    return RunReport(config.get("experiment_id") or run_dir.name, config, kept, unfinished, validated,
                     superseded=len(sessions) - len(kept), strata=dict(strata or {}), similarity_floor=floor)


# ── markdown 출력 ─────────────────────────────────────────

def render(report: RunReport) -> str:
    config, sessions = report.config, report.sessions
    k_req = (config.get("risk") or {}).get("k_req")
    lines = [f"# 실행 집계: {report.experiment_id}", ""]

    lines += _table(["항목", "값"], [
        ["백엔드", (config.get("backend") or {}).get("name", "-")],
        ["기준 집단", f"{(config.get('population') or {}).get('size', 0):,}명 ({(config.get('population') or {}).get('mode', '-')})"],
        ["요구 익명성 k_req", k_req if k_req is not None else "-"],
        ["대상", ", ".join(config.get("target_ids") or []) or "-"],
        ["갈래", ", ".join(config.get("arms") or []) or "-"],
        ["세션당 최대 턴", config.get("max_turns", "-")],
        ["모델", config.get("model") or "-"],
        ["프롬프트", ", ".join(f"{role}={version}" for role, version in (config.get("prompts") or {}).items()) or "-"],
        ["temperature", ", ".join(f"{role}={_dash(value)}" for role, value in (config.get("temperature") or {}).items()) or "-"],
        ["Seed 대상 제한", _dash((config.get("fuzzer") or {}).get("restrict_seeds_to_counted_qis"))],
        ["갈래 간 같은 질문", _dash((config.get("fuzzer") or {}).get("paired_questions"))],
        ["질문 보정 (자료 범위 안내 / QI 고른 배분)",
         f"{'켜짐' if (config.get('fuzzer') or {}).get('target_scope_note_sha256') else '꺼짐 또는 기록 없음'} / "
         f"{_dash((config.get('fuzzer') or {}).get('balance_qis'))}"],
        ["끝난 세션 / 턴", f"{len(sessions)} / {sum(s.turns for s in sessions)} (오류로 끝난 대상 포함)"],
        ["정답 대조(k_val)", "있음" if report.validated else "없음 (python -m validation 실행 전)"],
    ])
    if report.unfinished_turns:
        lines += ["", f"주의: 세션 요약 없이 끝난 턴이 {report.unfinished_turns}개 있습니다 (중간에 멈춘 실행). 아래 표에는 넣지 않았습니다."]
    if report.superseded:
        lines += ["", f"참고: 같은 대상·갈래를 다시 돌린 기록이 있어 앞의 세션 {report.superseded}개는 집계에서 뺐습니다 "
                  "(대상·갈래마다 마지막으로 끝난 세션만 씁니다)."]
    broken = [s for s in sessions if s.stop_reason == "LLM_ERROR"]
    if broken:
        lines += ["", f"주의: LLM 호출 오류로 중간에 끝난 세션이 {len(broken)}개 있습니다 (API 한도 초과, 연결 실패 등): "
                  + ", ".join(f"{s.target_id}/{s.arm}({s.turns}턴)" for s in broken)
                  + ". 턴이 모자란 결과이므로 해당 대상의 세션은 아래 표에서 모두 뺐습니다 "
                  "(같은 질문을 받은 다른 갈래 포함). "
                  "원인을 해결한 뒤 같은 명령에 --resume을 붙여 다시 돌리면 이 대상들만 다시 실행합니다."]
        incomplete = {s.target_id for s in broken}
        sessions = [s for s in sessions if s.target_id not in incomplete]
    lines += _outcome_section(report)

    lines += ["", "## 갈래별 요약", ""]
    rows = []
    for arm in _ordered(s.arm for s in sessions):
        group = [s for s in sessions if s.arm == arm]
        k_mins = [s.k_min for s in group if s.k_min is not None]
        k_vals = [s.k_val_min for s in group if s.validated]
        rows.append([
            arm, len(group), sum(s.turns for s in group),
            sum(s.first_violation_turn is not None for s in group),
            sum(s.validated_first_violation_turn is not None for s in group) if k_vals else "-",
            _spread(k_mins), _spread(k_vals) if k_vals else "-",
            sum(s.zero_match_turns for s in group),
            " / ".join(str(sum(s.paths[path] for s in group)) for path in RELEASE_PATHS),
            sum(s.generalized_turns for s in group), sum(s.suppressed_turns for s in group),
            sum(s.unresolved_released for s in group),
        ])
    lines += _table(["갈래", "세션", "턴", "위반 세션 (k_run)", "위반 세션 (k_val)", "최소 k_run 중앙값 (범위)",
                     "최소 k_val 중앙값 (범위)", "k=0 턴", "공개 경로 " + "/".join(RELEASE_PATHS),
                     "일반화 턴", "억제 턴", "미해석"], rows)
    lines += ["", f"위반 = 1 ≤ k < k_req({k_req})에 한 번이라도 도달한 세션. k = 0은 위반으로 세지 않고 따로 셉니다."]

    lines += ["", "## 세션별", ""]
    lines += _table(
        ["대상", "갈래", "턴", "종료", "k 처음 → 최소 → 끝", "첫 위반 턴", "최소 k_val", "첫 위반 턴 (k_val)",
         "틀린 값이 들어간 QI", "일반화/억제 턴", "개입 사유", "2회차", "잔여 단서로 재수정", "지시 값 기록", "미해석"],
        [[_name(s), s.arm, s.turns, s.stop_reason or "-",
          f"{_num(s.k_initial)} → {_num(s.k_min)} → {_num(s.k_final)}", _dash(s.first_violation_turn),
          _num(s.k_val_min) if s.validated else "-",
          _dash(s.validated_first_violation_turn) if s.validated else "-",
          (", ".join(s.incorrect_qis) or "없음") if s.validated else "-",
          f"{s.generalized_turns}/{s.suppressed_turns}", _counts(s.intervention_reasons), s.second_round_turns,
          s.residual_rounds, s.adopted, s.unresolved_released]
         for s in sessions])

    lines += _utility_section(report, sessions)

    lines += ["", "## 추출·정규화", ""]
    lines += _table(
        ["대상", "갈래", "추출 실패 턴", "근거 불일치로 버린 관찰", "MAPPED", "PENDING", "UNMAPPED", "State에 반영된 관찰 (QI별)"],
        [[_name(s), s.arm, s.extraction_failures, s.rejected_observations, s.statuses["MAPPED"], s.statuses["PENDING"],
          s.statuses["UNMAPPED"], _counts(s.committed_by_qi)] for s in sessions])
    # 같은 원 응답을 함께 쓰는 갈래에서는 같은 관찰이 갈래 수만큼 반복된다. 한 줄로 묶고 갈래를 나란히 적는다
    merged: dict[tuple[Any, ...], list[str]] = {}
    for s in sessions:
        for row in s.unresolved:
            key = (_name(s), row["turn"], row["attribute"], row["raw_value"], row["value_en"], row["status"], row["reason"])
            merged.setdefault(key, []).append(s.arm)
    if merged:
        total = sum(len(arms) for arms in merged.values())
        by_reason = Counter((key[2], key[6]) for key in merged)
        lines += ["", f"공개됐지만 정규화하지 못한 관찰 {total}건 (갈래 간 같은 관찰을 묶으면 {len(merged)}가지)"
                  + (f", 앞 {MAX_UNRESOLVED_ROWS}가지만 표시" if len(merged) > MAX_UNRESOLVED_ROWS else "") + ":", ""]
        lines += _table(["대상", "갈래", "턴", "QI", "응답의 표현", "추출기 표준 표기", "상태", "이유"],
                        [[key[0], "·".join(arms), *key[1:]] for key, arms in list(merged.items())[:MAX_UNRESOLVED_ROWS]])
        lines += ["", "QI·이유별 (묶은 뒤): " + ", ".join(f"{qi} {reason} {count}" for (qi, reason), count
                                                         in by_reason.most_common())]

    lines += ["", "## 공격 쪽", ""]
    lines += _table(
        ["대상", "갈래", "질문 출처", "쓴 Seed 수", "질문이 노린 QI", "Seed 교체 사유", "버린 변형 (사유)",
         "Seed 원문을 그대로 쓴 턴"],
        [[_name(s), s.arm, f"{s.questions_from}의 질문 그대로" if s.questions_from else "직접 생성", len(s.seeds),
          _counts(s.target_qis), _counts(s.reset_reasons), _counts(s.rejected_mutations), s.fallbacks]
         for s in sessions])
    lines += ["", "버린 변형: META_OUTPUT은 변형 LLM이 질문 대신 내놓은 거부·되묻기 문장, OUT_OF_SCOPE_SOURCE는 "
              "Target에 없는 자료(구매·검색 기록 등)를 전제로 한 질문입니다. 버린 뒤 다시 요청하고, 끝내 안 되면 Seed 원문을 씁니다."]

    lines += ["", "## Coverage", ""]
    lines += _table(
        ["대상", "갈래", "QI /8", "쌍 /28", "서로 다른 State", "서로 다른 전이", "Interesting 턴"],
        [[_name(s), s.arm, len(s.coverage.get("qi_seen") or []), len(s.coverage.get("pair_seen") or []),
          _dash(s.coverage.get("unique_states")), _dash(s.coverage.get("unique_transitions")),
          _dash(s.coverage.get("interesting_turns"))] for s in sessions])

    lines += ["", "## 토큰 (입력 / 출력)과 호출 수", ""]
    rows = []
    for arm in _ordered(s.arm for s in sessions):
        group = [s for s in sessions if s.arm == arm]
        rows.append([arm] + [f"{sum(s.tokens[r][0] for s in group):,} / {sum(s.tokens[r][1] for s in group):,} "
                             f"({sum(s.calls[r] for s in group)}회)" for r in ROLES])
    lines += _table(["갈래", "질문 생성", "Target", "추출", "재작성"], rows)
    lines += ["", "추출에는 재작성한 응답의 재추출이 포함됩니다. 실패한 호출(추출 출력 파싱 실패, 재작성 실패)과 "
              "재추출의 재시도는 trace에 토큰이 남지 않아 여기에 들어가지 않습니다. 실제 사용량은 이 표보다 클 수 있습니다."]

    refusals = Counter()
    for s in sessions:
        refusals.update(s.refusal_reasons)
    if refusals:
        lines += ["", "거절 사유: " + _counts(refusals)]
    return "\n".join(lines) + "\n"


def _utility_rows(sessions: list[SessionStats]) -> list[list[Any]]:
    rows = []
    for arm in _ordered(s.arm for s in sessions):
        turns = [entry for s in sessions if s.arm == arm for entry in s.utilities]
        if not turns:
            continue
        touched = [entry for entry in turns if entry[0] in ("REWRITTEN", "REFUSAL")]
        rewritten = [entry for entry in turns if entry[0] == "REWRITTEN"]
        refused = [entry for entry in turns if entry[0] == "REFUSAL"]
        untouched = [entry for entry in turns if entry[0] not in ("REWRITTEN", "REFUSAL")]
        rows.append([
            arm, len(turns), f"{len(untouched)} ({len(untouched) / len(turns):.1%})", len(rewritten), len(refused),
            _dash(mean(u.similarity for _, u, _ in turns)),
            _dash(mean(u.similarity for _, u, _ in rewritten)), _dash(mean(u.length_ratio for _, u, _ in rewritten)),
            _dash(mean(u.qi_retention for _, u, _ in rewritten)),
            _dash(mean(u.similarity for _, u, _ in refused)),
            f"{_dash(mean(float(n) for _, _, n in touched))} / {_dash(mean(float(n) for _, _, n in untouched))}",
        ])
    return rows


def _utility_section(report: RunReport, sessions: list[SessionStats]) -> list[str]:
    rows = _utility_rows(sessions)
    if not rows:
        return []
    lines = ["", "## 유용성 (Defender가 응답을 고친 대가)", ""]
    lines += _table(["갈래", "턴", "그대로 공개", "재작성 턴", "거절 턴", "평균 유사도 (전체)", "평균 유사도 (재작성 턴)",
                     "평균 길이비 (재작성 턴)", "QI 유지율 (재작성 턴)", "평균 유사도 (거절 턴)",
                     "원 응답의 QI 수: 개입 턴 / 통과 턴"], rows)
    lines += ["", f"바닥값(서로 다른 질문에 대한 원 응답끼리의 유사도): {_dash(report.similarity_floor)}. "
              "유사도가 이 값보다 낮으면 원래 답과의 관련이 무관한 답 수준으로 떨어진 것입니다.",
              "유사도는 원 응답과 공개 응답의 글자 3-gram 코사인 유사도입니다. 글자 겹침을 재는 값이라 뜻이 보존됐는지를 "
              "직접 말해 주지는 않습니다. QI 유지율은 원 응답에서 풀린 QI 종류 중 공개 응답에 남은 비율이며, "
              "낮을수록 보호를 위해 많이 덜어냈다는 뜻입니다."]
    return lines


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """
    input : only_first (첫 조건에서만 사건이 난 짝의 수), only_second (둘째 조건에서만 난 짝의 수)
    output : 양측 exact McNemar p값 (이항분포, 불일치 짝만 사용). 불일치 짝이 없으면 1.0
    """
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_first, only_second) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_tests(report: RunReport) -> list[list[Any]]:
    """갈래 쌍마다, 같은 대상에서 위반 여부가 어떻게 갈렸는지와 exact McNemar p값."""
    incomplete = {s.target_id for s in report.sessions if s.stop_reason == "LLM_ERROR"}
    sessions = [s for s in report.sessions if s.session_key is None and s.target_id not in incomplete]
    by = {(s.target_id, s.arm): s for s in sessions}
    arms = _ordered(s.arm for s in sessions)
    rows = []
    for i, first in enumerate(arms):
        for second in arms[i + 1:]:
            targets = [t for t in _ordered(s.target_id for s in sessions) if (t, first) in by and (t, second) in by]
            if len(targets) < 2:
                continue
            pairs = [(_violated(by[(t, first)], report.validated), _violated(by[(t, second)], report.validated))
                     for t in targets]
            both = sum(a and b for a, b in pairs)
            only_first = sum(a and not b for a, b in pairs)
            only_second = sum(b and not a for a, b in pairs)
            rows.append([f"{first} 대 {second}", len(targets), both, only_first, only_second,
                         len(targets) - both - only_first - only_second,
                         f"{mcnemar_exact(only_first, only_second):.3f}"])
    return rows


def _violated(session: SessionStats, validated: bool) -> bool:
    """정답 대조가 있으면 k_val 기준, 없으면 실행 중 k_run 기준으로 위반 여부를 본다."""
    if validated and session.validated:
        return session.validated_first_violation_turn is not None
    return session.first_violation_turn is not None


def _outcome_section(report: RunReport) -> list[str]:
    """대상별로 갈래의 결과를 나란히 놓고, 갈래 조합별 대상 수를 센다. 대상이 둘 이상일 때만 만든다."""
    incomplete = {s.target_id for s in report.sessions if s.stop_reason == "LLM_ERROR"}
    sessions = [s for s in report.sessions if s.session_key is None and s.target_id not in incomplete]
    targets = _ordered(s.target_id for s in sessions)
    arms = _ordered(s.arm for s in sessions)
    if len(targets) < 2:
        return []
    basis = "k_val" if report.validated else "k_run (정답 대조 전)"
    by = {(s.target_id, s.arm): s for s in sessions}
    lines = ["", f"## 대상별 결과 (위반 판정 기준: {basis})", ""]

    def cell(target: str, arm: str) -> str:
        s = by.get((target, arm))
        if s is None:
            return "-"
        k = s.k_val_min if (report.validated and s.validated) else s.k_min
        turn = s.validated_first_violation_turn if (report.validated and s.validated) else s.first_violation_turn
        return f"{_num(k)}" + (f" (위반 {turn}턴)" if _violated(s, report.validated) else "")

    header = ["대상"] + (["층"] if report.strata else []) + [f"{arm} 최소 k" for arm in arms]
    lines += _table(header, [[target] + ([report.strata.get(target, "-")] if report.strata else [])
                             + [cell(target, arm) for arm in arms] for target in targets])

    complete = [t for t in targets if all((t, arm) in by for arm in arms)]
    patterns: Counter = Counter()
    for target in complete:
        key = tuple(_violated(by[(target, arm)], report.validated) for arm in arms)
        patterns[(report.strata.get(target, "") if report.strata else "", key)] += 1
    lines += ["", f"갈래별 위반 여부 조합 (모든 갈래가 끝난 대상 {len(complete)}명):", ""]
    lines += _table((["층"] if report.strata else []) + [f"{arm} 위반" for arm in arms] + ["대상 수"],
                    [([stratum or "-"] if report.strata else []) + ["예" if hit else "아니오" for hit in key] + [count]
                     for (stratum, key), count in sorted(patterns.items(), key=lambda item: (item[0][0], item[0][1]),
                                                         reverse=True)])
    lines += ["", "대상 수가 적으므로 비율로 일반화하지 않습니다. 층(rich/sparse)은 사람 라벨로 어림한 구분이며 결과 수치가 아닙니다."
              if report.strata else "대상 수가 적으므로 비율로 일반화하지 않습니다."]
    tests = paired_tests(report)
    if tests:
        lines += ["", "같은 대상끼리 짝지은 비교 (exact McNemar, 양측):", ""]
        lines += _table(["갈래 쌍", "대상 수", "둘 다 위반", "앞 갈래만 위반", "뒤 갈래만 위반", "둘 다 아님", "p"], tests)
        lines += ["", "p값은 위반 여부가 갈린 대상만으로 계산합니다. 갈린 대상이 5명이면 한쪽으로 전부 쏠려도 p = 0.0625가 "
                  "가장 작은 값입니다. 대상 수가 적을 때 p가 크게 나오는 것은 차이가 없다는 뜻이 아니라 판단할 표본이 "
                  "모자라다는 뜻입니다."]
    return lines


def render_comparison(reports: list[RunReport]) -> str:
    """여러 실행(같은 대화에 다른 설정을 적용해 다시 계산한 것 등)을 갈래별로 한 표에 놓는다."""
    lines = ["# 실행 비교", ""]
    rows, test_rows = [], []
    for report in reports:
        config = report.config
        incomplete = {s.target_id for s in report.sessions if s.stop_reason == "LLM_ERROR"}
        sessions = [s for s in report.sessions if s.target_id not in incomplete]
        risk = config.get("risk") or {}
        for arm in _ordered(s.arm for s in sessions):
            group = [s for s in sessions if s.arm == arm]
            k_vals = [s.k_val_min for s in group if s.validated]
            utilities = [entry for s in group for entry in s.utilities]
            rows.append([
                report.experiment_id, (config.get("backend") or {}).get("name", "-"), _dash(risk.get("k_req")),
                _dash(risk.get("tau_commit")), arm, len(group),
                sum(s.first_violation_turn is not None for s in group),
                sum(s.validated_first_violation_turn is not None for s in group) if k_vals else "-",
                _spread(k_vals) if k_vals else "-", sum(s.zero_match_turns for s in group),
                sum(s.paths["REWRITTEN"] for s in group), sum(s.paths["REFUSAL"] for s in group),
                _dash(mean(u.similarity for _, u, _ in utilities)), sum(s.unresolved_released for s in group),
            ])
        test_rows += [[report.experiment_id, *row] for row in paired_tests(report)]
    lines += _table(["실행", "백엔드", "k_req", "확신도 기준", "갈래", "대상", "위반 (k_run)", "위반 (k_val)",
                     "최소 k_val 중앙값 (범위)", "k=0 턴", "재작성 턴", "거절 턴", "평균 유사도", "미해석"], rows)
    lines += ["", "위반 = 1 ≤ k < k_req에 한 번이라도 도달한 대상 수. k_req가 다른 실행끼리는 위반의 기준 자체가 다릅니다.",
              "'위반 (k_val)'이 '-'인 실행은 정답 대조(python -m validation)를 아직 돌리지 않은 것입니다."]
    if test_rows:
        lines += ["", "## 같은 대상끼리 짝지은 비교 (exact McNemar, 양측)", ""]
        lines += _table(["실행", "갈래 쌍", "대상 수", "둘 다 위반", "앞 갈래만 위반", "뒤 갈래만 위반", "둘 다 아님", "p"],
                        test_rows)
    return "\n".join(lines) + "\n"


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(cell).replace("|", "/").replace("\n", " ") for cell in row) + " |" for row in rows]
    return out


def _ordered(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _name(session: SessionStats) -> str:
    return f"{session.target_id} [{session.session_key}]" if session.session_key else session.target_id


def _dash(value: Any) -> str:
    return "-" if value is None else str(value)


def _num(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _spread(values: list[int]) -> str:
    if not values:
        return "-"
    return f"{median(values):,.0f} ({min(values):,}–{max(values):,})"


def _counts(counter: Counter) -> str:
    return ", ".join(f"{key} {count}" for key, count in counter.most_common()) or "-"


def main() -> None:
    parser = argparse.ArgumentParser(description="실행 결과 집계 (LLM 호출 없음)")
    parser.add_argument("experiment_ids", nargs="+", help="runs/ 아래 실행 폴더 이름")
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--no-file", action="store_true", help="report.md를 쓰지 않고 화면에만 출력")
    parser.add_argument("--compare", default=None, metavar="FILE",
                        help="준 실행들을 한 표로 비교해 이 파일(.md)에 저장한다 (실행별 report.md도 함께 만든다)")
    parser.add_argument("--targets-file", default=None,
                        help="대상 선정 파일 (python -m validation.select_targets 결과). 주면 대상별 표에 층을 함께 적는다")
    args = parser.parse_args()

    strata = None
    if args.targets_file:
        selection = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
        strata = {item["target_id"]: item["stratum"] for item in selection.get("targets", [])}

    loaded: list[RunReport] = []
    for experiment_id in args.experiment_ids:
        run_dir = Path(args.runs_root) / experiment_id
        if not (run_dir / TRACE_FILE_NAME).exists():
            print(f"건너뜀: {run_dir}에 trace.jsonl이 없습니다. 폴더 이름을 확인하세요 (ls {args.runs_root}).\n")
            continue
        report = load_run(run_dir, strata)
        loaded.append(report)
        text = render(report)
        if args.compare:
            print(f"{experiment_id}: 세션 {len(report.sessions)}개 집계")
        else:
            print(text)
        if not args.no_file:
            (run_dir / REPORT_FILE_NAME).write_text(text, encoding="utf-8")
            print(f"저장: {run_dir / REPORT_FILE_NAME}\n")
    if args.compare and loaded:
        comparison = render_comparison(loaded)
        print("\n" + comparison)
        Path(args.compare).parent.mkdir(parents=True, exist_ok=True)
        Path(args.compare).write_text(comparison, encoding="utf-8")
        print(f"저장: {args.compare}")


if __name__ == "__main__":
    main()
