# SynthPAI comments 원본(synthpai.jsonl)을 커밋 고정 URL에서 내려받고 sha256을 검증하는 파일
#
# 실행: 프로젝트 루트에서
#   python -m target_llm.download          # 없을 때만 받기
#   python -m target_llm.download --force  # 있어도 다시 받기
#
# 라이브러리 코드(corpus.py)가 몰래 네트워크를 쓰지 않도록 다운로드는 이 스크립트로만 한다.
# 받는 중에는 임시 파일(.part)에 쓰고, 해시가 맞을 때만 최종 경로로 옮긴다.
# 중간에 끊기거나 내용이 달라도 깨진 파일이 정식 경로에 남지 않는다.

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import requests

from target_llm.corpus import (
    DEFAULT_COMMENTS_PATH,
    SYNTHPAI_COMMENTS_SHA256,
    SYNTHPAI_COMMENTS_URL,
    SYNTHPAI_COMMIT,
)

CHUNK_BYTES = 1 << 16   # 64KB씩 받아 메모리에 20MB를 한꺼번에 올리지 않는다
TIMEOUT_SEC = 60


def download_comments(dest: Path = DEFAULT_COMMENTS_PATH, *, force: bool = False) -> Path:
    """
    input : dest (저장 경로), force (True면 기존 파일이 있어도 다시 받음)
    output : 저장된 파일 경로

    이미 있고 해시가 맞으면 다시 받지 않는다. 해시가 다르면 force 없이도 다시 받는다.
    """
    dest = Path(dest)
    if dest.exists() and not force:
        if _sha256_of(dest) == SYNTHPAI_COMMENTS_SHA256:
            print(f"이미 있고 해시가 일치합니다: {dest}")
            return dest
        print("기존 파일의 해시가 다릅니다. 다시 받습니다.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    hasher = hashlib.sha256()

    print(f"다운로드: {SYNTHPAI_COMMENTS_URL}")
    with requests.get(SYNTHPAI_COMMENTS_URL, stream=True, timeout=TIMEOUT_SEC) as response:
        response.raise_for_status()
        with tmp_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                file.write(chunk)
                hasher.update(chunk)

    digest = hasher.hexdigest()
    if digest != SYNTHPAI_COMMENTS_SHA256:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"받은 파일의 해시가 다릅니다.\n  기대: {SYNTHPAI_COMMENTS_SHA256}\n  실제: {digest}"
        )

    tmp_path.replace(dest)
    print(f"저장 완료: {dest} (SynthPAI 커밋 {SYNTHPAI_COMMIT[:7]}, sha256 일치)")
    return dest


def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SynthPAI comments 원본 다운로드")
    parser.add_argument("--force", action="store_true", help="기존 파일이 있어도 다시 받기")
    args = parser.parse_args()
    download_comments(force=args.force)
