import json
from dataclasses import asdict, dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "log"
DEFAULT_LOG_PATH = LOG_DIR / "trace_log.jsonl"


@dataclass(frozen=True)
class TurnTrace:
    """
    turn
    → 몇 번째 turn인지
    prompt
    → 이번 turn에 사용된 prompt
    k_before
    → 공격 전 후보군 크기
    k_after
    → 공격 후 후보군 크기
    strategy
    → select_mutation_strategy()가 결정한 다음 mutation 방향
    """
    turn: int
    prompt: str
    k_before: int
    k_after: int
    strategy: str


def log_turn(
    turn: int,
    prompt: str,
    k_before: int,
    k_after: int,
    strategy: str,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> TurnTrace:
    """
    input : 한 turn을 구성하는 값들 (PROMPT -> K 변화 -> Strategy 흐름 확인용 최소 값)
    output : TurnTrace (기록된 값을 담은 객체)

    한 turn의 결과를 TurnTrace로 묶어 log_path에 JSONL 형식으로 한 줄 추가합니다.
    """
    trace = TurnTrace(
        turn=turn,
        prompt=prompt,
        k_before=k_before,
        k_after=k_after,
        strategy=strategy,
    )

    record = asdict(trace)

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return trace


def load_trace(
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> list[dict]:
    """
    input : log_path (읽어올 trace 로그 파일 경로)
    output : list[dict] (저장된 모든 Turn 기록)

    log_path에 저장된 JSONL 로그를 한 줄씩 읽어 dict 리스트로 반환합니다.
    로그 파일이 없으면 빈 리스트를 반환합니다.
    """
    path = Path(log_path)

    if not path.exists():
        return []

    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    return records


def clear_trace(
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> None:
    """
    input : log_path (초기화할 trace 로그 파일 경로)
    output : None

    log_path에 있는 trace 로그 파일을 삭제합니다.
    파일이 없으면 아무 작업도 하지 않습니다.
    """
    path = Path(log_path)

    if path.exists():
        path.unlink()


if __name__ == "__main__":
    clear_trace()

    log_turn(
        turn=1,
        prompt="PROMPT1 내용",
        k_before=300,
        k_after=80,
        strategy="intensify",
    )

    log_turn(
        turn=2,
        prompt="PROMPT2 내용",
        k_before=80,
        k_after=30,
        strategy="refine",
    )

    print("------")

    traces = load_trace()

    for trace in traces:
        print(trace)
