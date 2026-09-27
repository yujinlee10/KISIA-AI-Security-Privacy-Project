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
    input_prompt
    → 이번 turn에 입력으로 사용된 prompt (직전 turn의 output_prompt. Turn 0에는 없으므로 None)
    k_before
    → 공격 전 후보군 크기 (Turn 0에는 없으므로 None)
    k_after
    → 공격 후 후보군 크기 (Turn 0에는 없으므로 None)
    strategy
    → select_mutation_strategy()가 결정한 다음 mutation 방향 (Turn 0에는 없으므로 None)
    output_prompt
    → 이번 turn에서 생성된 prompt (abort로 생성되지 못했으면 None)
    """
    turn: int
    input_prompt: str | None = None
    k_before: int | None = None
    k_after: int | None = None
    strategy: str | None = None
    output_prompt: str | None = None


def log_turn(
    turn: int,
    input_prompt: str | None = None,
    k_before: int | None = None,
    k_after: int | None = None,
    strategy: str | None = None,
    output_prompt: str | None = None,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> TurnTrace:
    """
    input : 한 turn을 구성하는 값들 (input_prompt -> K 변화 -> strategy -> output_prompt 흐름 확인용 최소 값)
    output : TurnTrace (기록된 값을 담은 객체)

    한 turn의 결과를 TurnTrace로 묶어 log_path에 JSONL 형식으로 한 줄 추가합니다.
    Turn 0은 새로운 Fuzzer 실행의 시작이므로, 기록 전에 기존 로그 파일을
    초기화(clear_trace)한 뒤 첫 줄로 기록합니다.
    Turn 0은 초기 Prompt 생성 단계라 input_prompt/k_before/k_after/strategy가
    존재하지 않으므로, 값이 잘못 전달되더라도 무조건 None으로 기록합니다.
    """
    if turn == 0:
        clear_trace(log_path)

        trace = TurnTrace(
            turn=0,
            input_prompt=None,
            k_before=None,
            k_after=None,
            strategy=None,
            output_prompt=output_prompt,
        )
    else:
        trace = TurnTrace(
            turn=turn,
            input_prompt=input_prompt,
            k_before=k_before,
            k_after=k_after,
            strategy=strategy,
            output_prompt=output_prompt,
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
    # 테스트 0: Turn 0이 로그 파일 자체를 초기화하는지 확인용으로
    # 미리 더미 로그를 하나 남겨둔다.
    log_turn(
        turn=1,
        input_prompt="이전 실행에서 남은 프롬프트",
        k_before=999,
        k_after=999,
        strategy="diversify",
        output_prompt="이전 실행 결과",
    )

    # 테스트 1: Turn 0 → 기존 로그(위 더미 포함)가 초기화되고 이 한 줄만 남아야 함
    log_turn(
        turn=0,
        output_prompt="초기 프롬프트",
    )

    # 테스트 2: Turn 0에 잘못된 값 전달 → None으로 저장되고, 다시 초기화되어 이 줄만 남아야 함
    log_turn(
        turn=0,
        input_prompt="이 값은 저장되면 안 됨",
        k_before=300,
        k_after=80,
        strategy="intensify",
        output_prompt="초기 프롬프트",
    )

    # 테스트 3: Turn 1 → Turn 0 로그 뒤에 append 되어야 함
    log_turn(
        turn=1,
        input_prompt="초기 프롬프트",
        k_before=300,
        k_after=80,
        strategy="intensify",
        output_prompt="프롬프트1",
    )

    print("------")

    traces = load_trace()

    for trace in traces:
        print(trace)
