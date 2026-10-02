# target_llm/corpus.py 단위 테스트 — 정답 필드 제거, persona 분리, 형식·무결성 오류

from __future__ import annotations

import json

import pytest

from target_llm.corpus import (
    DEFAULT_COMMENTS_PATH,
    SYNTHPAI_COMMENTS_SHA256,
    SYNTHPAI_COMMIT,
    CorpusFormatError,
    CorpusIntegrityError,
    PersonaCorpus,
    TargetNotFoundError,
)
from tests.target_llm.sample_data import GT_MARKER, make_record


# ── GT 비유출 ──────────────────────────────────────────────

def test_ground_truth_fields_never_survive_loading(sample_corpus):
    """profile·reviews·guesses·username 표식이 적재된 객체 어디에도 없어야 한다."""
    for target_id in sample_corpus.target_ids():
        for comment in sample_corpus.comments_for(target_id):
            assert GT_MARKER not in repr(comment)
            assert set(vars(comment)) == {"comment_id", "target_id", "text", "position"}


# ── persona 분리 ───────────────────────────────────────────

def test_comments_are_grouped_by_author_in_original_order(sample_corpus):
    pers1 = sample_corpus.comments_for("pers1")
    assert [c.comment_id for c in pers1] == ["c1", "c3", "c4"]
    assert [c.position for c in pers1] == [0, 1, 2]
    assert all(c.target_id == "pers1" for c in pers1)


def test_target_ids_and_counts(sample_corpus):
    assert sample_corpus.target_ids() == ("pers1", "pers2")
    assert len(sample_corpus) == 5
    assert sample_corpus.info.persona_count == 2
    assert "pers1" in sample_corpus
    assert "pers999" not in sample_corpus


def test_unknown_target_raises(sample_corpus):
    with pytest.raises(TargetNotFoundError):
        sample_corpus.comments_for("pers999")


# ── 형식 오류 ──────────────────────────────────────────────

def _write(tmp_path, lines):
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_non_json_line_reports_line_number(tmp_path):
    path = _write(tmp_path, [json.dumps(make_record("pers1", "c1", "ok")), "{broken"])
    with pytest.raises(CorpusFormatError) as exc:
        PersonaCorpus.load(path, expected_sha256=None)
    assert "2번째 줄" in str(exc.value)


@pytest.mark.parametrize("field", ["author", "id", "text"])
def test_missing_required_field_raises(tmp_path, field):
    record = make_record("pers1", "c1", "ok")
    record[field] = ""
    path = _write(tmp_path, [json.dumps(record)])
    with pytest.raises(CorpusFormatError) as exc:
        PersonaCorpus.load(path, expected_sha256=None)
    assert field in str(exc.value)


def test_duplicate_comment_id_raises(tmp_path):
    path = _write(tmp_path, [
        json.dumps(make_record("pers1", "c1", "a")),
        json.dumps(make_record("pers2", "c1", "b")),
    ])
    with pytest.raises(CorpusFormatError):
        PersonaCorpus.load(path, expected_sha256=None)


def test_blank_lines_are_ignored(tmp_path):
    path = _write(tmp_path, ["", json.dumps(make_record("pers1", "c1", "a")), "   "])
    assert len(PersonaCorpus.load(path, expected_sha256=None)) == 1


# ── 무결성 ────────────────────────────────────────────────

def test_hash_mismatch_raises(sample_jsonl):
    with pytest.raises(CorpusIntegrityError):
        PersonaCorpus.load(sample_jsonl, expected_sha256="0" * 64)


def test_missing_file_explains_download_command(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        PersonaCorpus.load(tmp_path / "nope.jsonl")
    assert "python -m target_llm.download" in str(exc.value)


def test_unverified_load_records_no_source_commit(sample_corpus):
    assert sample_corpus.info.source_commit is None
    assert len(sample_corpus.info.sha256) == 64


# ── 실제 데이터 (다운로드되어 있을 때만) ───────────────────

@pytest.mark.skipif(not DEFAULT_COMMENTS_PATH.exists(), reason="synthpai.jsonl 미다운로드")
def test_real_synthpai_loads_with_pinned_hash():
    corpus = PersonaCorpus.load()
    assert corpus.info.sha256 == SYNTHPAI_COMMENTS_SHA256
    assert corpus.info.source_commit == SYNTHPAI_COMMIT
    assert corpus.info.comment_count == 7823
    assert corpus.info.persona_count == 300
    # risk_defender demo의 기본 target과 같은 pers1이 있어야 연동이 된다
    assert len(corpus.comments_for("pers1")) == 67
