# SynthPAI comments 원본을 읽어 persona별 글 목록(Persona Knowledge Store)으로 만드는 파일
#
# 원본 synthpai.jsonl의 레코드 한 줄에는 다음이 함께 들어 있다.
#   author, id, text                 → 우리가 쓰는 것 (persona가 직접 쓴 글)
#   profile                          → 정답(GT). Reference/Evaluation 용도라 여기서 버린다.
#   reviews                          → 사람이 붙인 속성 라벨. Extractor 평가용이라 여기서 버린다.
#   guesses                          → 모델 추정값. 쓰지 않는다.
#   username                         → 직접식별자. Target 문맥에 넣지 않는다.
#   parent_id, thread_id, children   → 다른 사람 글과의 연결. 지금은 쓰지 않는다.
#
# 화이트리스트 방식: 필요한 세 필드만 골라 담고 나머지는 객체에 아예 남기지 않는다.
# "버릴 필드 목록"을 관리하는 방식(블랙리스트)은 원본에 새 필드가 생기면 그대로 새어 들어온다.
#
# 원본 데이터 특성 (2026-10-01, 커밋 ba9edc9 기준 확인값)
#   comments 7,823개 / persona 300명 / persona당 2~144개 (중앙값 16)
#   전부 영어. 같은 author의 profile 중 style·guess_feature만 레코드마다 다르고 QI 값은 동일하다.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from core.config import PROJECT_ROOT


# ──────────────────────────────────────────────────────────────
# 데이터 출처 (버전 고정)
#   main 브랜치 URL을 쓰면 원작자가 파일을 바꿀 때 실험 재현성이 깨진다.
#   커밋을 고정하고 sha256으로 내용까지 확인한다. (D1: corpus hash 기록)
# ──────────────────────────────────────────────────────────────

SYNTHPAI_REPO = "eth-sri/SynthPAI"
SYNTHPAI_COMMIT = "ba9edc9c8aa1d398bc43bb619a8d859351df9d9c"
SYNTHPAI_COMMENTS_URL = (
    f"https://raw.githubusercontent.com/{SYNTHPAI_REPO}/{SYNTHPAI_COMMIT}/data/synthpai.jsonl"
)
SYNTHPAI_COMMENTS_SHA256 = "026bfd2b94ecd9e3f3b9bf1cf95a85c42f4e37505144c35d74ec0d4384d87ad4"

# 원본 파일 저장 위치. .gitignore로 커밋에서 제외된다 (정답이 섞인 원본이므로).
DEFAULT_COMMENTS_PATH = PROJECT_ROOT / "data" / "synthpai" / "synthpai.jsonl"


# ──────────────────────────────────────────────────────────────
# 예외
# ──────────────────────────────────────────────────────────────

class CorpusFormatError(ValueError):
    """원본 줄이 JSON이 아니거나 필수 필드가 없을 때. 몇 번째 줄인지 함께 알려준다."""


class CorpusIntegrityError(ValueError):
    """파일 sha256이 고정값과 다를 때. 다른 버전의 데이터로 실험하는 것을 막는다."""


class TargetNotFoundError(KeyError):
    """요청한 target_id의 글이 corpus에 없을 때."""


# ──────────────────────────────────────────────────────────────
# 데이터 객체
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PersonaComment:
    """
    comment_id
    → 원본 id (재현·로그 추적용)
    target_id
    → 작성한 persona (원본 author, 예: pers1)
    text
    → persona가 쓴 글 본문. Target 문맥에 들어가는 유일한 내용이다.
    position
    → 이 persona 글 중 원본 파일에서 몇 번째인지 (0부터). 검색 결과 순서를 결정적으로 만든다.
    """
    comment_id: str
    target_id: str
    text: str
    position: int


@dataclass(frozen=True)
class CorpusInfo:
    """
    path / sha256
    → 실제로 읽은 파일과 그 해시 (run 로그에 남겨 어떤 데이터로 돌렸는지 기록)
    source_commit
    → 고정한 SynthPAI 커밋. 해시 검증을 끈 경우 None
    comment_count / persona_count
    → 적재된 글 수와 persona 수
    """
    path: str
    sha256: str
    source_commit: str | None
    comment_count: int
    persona_count: int


# ──────────────────────────────────────────────────────────────
# Persona Knowledge Store
# ──────────────────────────────────────────────────────────────

class PersonaCorpus:
    """
    persona별 글 목록을 메모리에 들고 있는 읽기 전용 저장소.

    target_id → 글 튜플 dict로 보관하므로 persona 하나를 꺼내는 비용은 O(1)이다.
    전체 텍스트가 약 1MB라 한 번 적재해 두고 run 전체에서 재사용한다.
    """

    def __init__(self, comments_by_target: dict[str, tuple[PersonaComment, ...]], info: CorpusInfo) -> None:
        self._comments_by_target = comments_by_target
        self._info = info

    @property
    def info(self) -> CorpusInfo:
        return self._info

    def target_ids(self) -> tuple[str, ...]:
        """
        input : 없음
        output : 정렬된 target_id 튜플
        """
        return tuple(sorted(self._comments_by_target))

    def comments_for(self, target_id: str) -> tuple[PersonaComment, ...]:
        """
        input : target_id
        output : 해당 persona가 쓴 글 튜플 (원본 순서)

        이 메서드가 strict target filter의 출발점이다. 다른 persona 글은 반환하지 않는다.
        """
        try:
            return self._comments_by_target[target_id]
        except KeyError:
            raise TargetNotFoundError(f"corpus에 없는 target_id입니다: {target_id}") from None

    def __contains__(self, target_id: object) -> bool:
        return target_id in self._comments_by_target

    def __len__(self) -> int:
        return self._info.comment_count

    # ── 로딩 ──────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_COMMENTS_PATH,
        *,
        expected_sha256: str | None = SYNTHPAI_COMMENTS_SHA256,
    ) -> "PersonaCorpus":
        """
        input : path (synthpai.jsonl 경로), expected_sha256 (None이면 해시 검증 생략 — 테스트용)
        output : PersonaCorpus

        파일을 한 번만 읽으면서 해시 계산과 파싱을 같이 한다 (20MB 파일을 두 번 읽지 않음).
        해시가 다르면 파싱 결과를 버리고 CorpusIntegrityError를 올린다.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"SynthPAI comments 파일이 없습니다: {path}\n"
                f"프로젝트 루트에서 'python -m target_llm.download'를 먼저 실행하세요."
            )

        hasher = hashlib.sha256()
        grouped: dict[str, list[PersonaComment]] = {}
        seen_ids: set[str] = set()
        comment_count = 0

        with path.open("rb") as file:
            for line_no, raw_line in enumerate(file, start=1):
                hasher.update(raw_line)
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue

                comment = _parse_line(line, line_no, grouped)
                if comment.comment_id in seen_ids:
                    raise CorpusFormatError(f"{line_no}번째 줄: 중복된 comment id {comment.comment_id}")
                seen_ids.add(comment.comment_id)

                grouped.setdefault(comment.target_id, []).append(comment)
                comment_count += 1

        digest = hasher.hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise CorpusIntegrityError(
                f"파일 해시가 고정값과 다릅니다.\n  기대: {expected_sha256}\n  실제: {digest}\n"
                f"'python -m target_llm.download --force'로 다시 받으세요."
            )

        info = CorpusInfo(
            path=str(path),
            sha256=digest,
            source_commit=SYNTHPAI_COMMIT if expected_sha256 == SYNTHPAI_COMMENTS_SHA256 else None,
            comment_count=comment_count,
            persona_count=len(grouped),
        )
        frozen = {target_id: tuple(items) for target_id, items in grouped.items()}
        return cls(frozen, info)


# ──────────────────────────────────────────────────────────────
# 내부 함수
# ──────────────────────────────────────────────────────────────

def _parse_line(line: str, line_no: int, grouped: dict[str, list[PersonaComment]]) -> PersonaComment:
    """
    input : JSON 한 줄, 줄 번호, 지금까지 모은 persona별 글 (position 계산용)
    output : PersonaComment

    필요한 세 필드(author, id, text)만 꺼낸다. 원본 dict는 이 함수 밖으로 나가지 않는다.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CorpusFormatError(f"{line_no}번째 줄이 JSON이 아닙니다: {exc.msg}") from None

    if not isinstance(record, dict):
        raise CorpusFormatError(f"{line_no}번째 줄이 객체가 아닙니다.")

    target_id = record.get("author")
    comment_id = record.get("id")
    text = record.get("text")

    for name, value in (("author", target_id), ("id", comment_id), ("text", text)):
        if not isinstance(value, str) or not value.strip():
            raise CorpusFormatError(f"{line_no}번째 줄: '{name}' 필드가 없거나 비어 있습니다.")

    position = len(grouped.get(target_id, ()))
    return PersonaComment(
        comment_id=comment_id,
        target_id=target_id,
        text=text,
        position=position,
    )
