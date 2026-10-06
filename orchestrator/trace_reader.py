# trace.jsonl을 세션 단위로 나눠 읽는다 (집계와 사후 검증이 같은 기준으로 세션을 가르도록)
#
# 세션의 끝은 session_end 줄이다. 다만 실행이 중간에 멈추면(Ctrl-C 등) session_end 없이 턴만 남고,
# 같은 experiment_id로 다시 돌리면 그 뒤에 새 세션의 턴이 이어 붙는다. 그래서 다음 경우에도 세션을 끊는다.
#   · 대상·갈래·session_key가 바뀌었다
#   · 턴 번호가 앞 턴보다 커지지 않았다 (새 세션이 1턴부터 다시 시작)
# 끊긴 세션은 end가 None이다.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class TraceSession:
    """turns → 이 세션의 turn 줄들 (순서대로) / end → session_end 줄. 중간에 멈춘 세션이면 None"""
    turns: tuple[dict[str, Any], ...]
    end: dict[str, Any] | None

    @property
    def finished(self) -> bool:
        return self.end is not None

    @property
    def head(self) -> dict[str, Any]:
        """대상·갈래를 읽을 줄 (첫 턴. 턴 없이 끝난 세션이면 session_end 줄)."""
        return self.turns[0] if self.turns else (self.end or {})


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def arm_of(item: dict[str, Any]) -> str | None:
    return item.get("arm") or item.get("condition")


def _identity(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    return item.get("target_id"), arm_of(item), item.get("session_key")


def iter_sessions(trace_path: str | Path) -> Iterator[TraceSession]:
    """
    input : trace_path (runs/<experiment_id>/trace.jsonl)
    output : TraceSession을 파일에 적힌 순서대로
    """
    turns: list[dict[str, Any]] = []
    for item in read_jsonl(trace_path):
        kind = item.get("type")
        if kind == "turn":
            if turns and (_identity(item) != _identity(turns[-1]) or item["turn"] <= turns[-1]["turn"]):
                yield TraceSession(tuple(turns), None)
                turns = []
            turns.append(item)
        elif kind == "session_end":
            if turns and _identity(item) != _identity(turns[-1]):
                yield TraceSession(tuple(turns), None)
                turns = []
            yield TraceSession(tuple(turns), item)
            turns = []
    if turns:
        yield TraceSession(tuple(turns), None)
