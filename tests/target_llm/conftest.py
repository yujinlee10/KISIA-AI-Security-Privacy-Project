# target_llm 테스트 공용 fixture (표본 데이터 자체는 sample_data.py에 있다)

from __future__ import annotations

import json

import pytest

from target_llm.corpus import PersonaCorpus
from tests.target_llm.sample_data import SAMPLE_RECORDS


@pytest.fixture
def sample_jsonl(tmp_path):
    """표본 레코드를 원본과 같은 JSONL 형식으로 임시 파일에 쓴다."""
    path = tmp_path / "synthpai.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in SAMPLE_RECORDS) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_corpus(sample_jsonl):
    """해시 검증 없이 표본 파일을 적재한 corpus."""
    return PersonaCorpus.load(sample_jsonl, expected_sha256=None)
