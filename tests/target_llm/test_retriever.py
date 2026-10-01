# target_llm/retriever.py 단위 테스트 — strict target filter, 글자 수 상한

from __future__ import annotations

import pytest

from target_llm.corpus import CorpusInfo, PersonaComment, PersonaCorpus
from target_llm.retriever import ContaminationError, PersonaScopeRetriever


def test_returns_only_target_comments(sample_corpus):
    result = PersonaScopeRetriever(sample_corpus).retrieve("pers1", "질문")

    assert result.comment_ids == ("c1", "c3", "c4")
    assert all(c.target_id == "pers1" for c in result.comments)
    assert result.candidate_count == 3
    assert result.context_truncated is False
    assert result.retriever_name == "persona_scope"


def test_other_persona_text_is_not_in_result(sample_corpus):
    result = PersonaScopeRetriever(sample_corpus).retrieve("pers1", "질문")
    joined = " ".join(c.text for c in result.comments)
    assert "hospital" not in joined      # pers2의 글
    assert "bowling" not in joined       # pers2의 글


def test_result_is_deterministic(sample_corpus):
    retriever = PersonaScopeRetriever(sample_corpus)
    assert retriever.retrieve("pers1", "a") == retriever.retrieve("pers1", "다른 질문")


def test_char_limit_keeps_whole_comments_only(sample_corpus):
    first_len = len(sample_corpus.comments_for("pers1")[0].text)
    retriever = PersonaScopeRetriever(sample_corpus, max_context_chars=first_len + 5)

    result = retriever.retrieve("pers1", "질문")

    assert result.comment_ids == ("c1",)
    assert result.context_chars == first_len
    assert result.context_truncated is True


def test_char_limit_smaller_than_first_comment_returns_empty(sample_corpus):
    result = PersonaScopeRetriever(sample_corpus, max_context_chars=3).retrieve("pers1", "질문")
    assert result.comments == ()
    assert result.context_truncated is True


def test_invalid_limit_rejected(sample_corpus):
    with pytest.raises(ValueError):
        PersonaScopeRetriever(sample_corpus, max_context_chars=0)


def test_contamination_is_detected():
    """corpus가 잘못 묶여 다른 persona 글이 들어오면 검색 단계에서 멈춰야 한다."""
    broken = PersonaCorpus(
        {"pers1": (
            PersonaComment("c1", "pers1", "mine", 0),
            PersonaComment("c9", "pers2", "not mine", 1),
        )},
        CorpusInfo(path="x", sha256="0" * 64, source_commit=None, comment_count=2, persona_count=1),
    )
    with pytest.raises(ContaminationError):
        PersonaScopeRetriever(broken).retrieve("pers1", "질문")
