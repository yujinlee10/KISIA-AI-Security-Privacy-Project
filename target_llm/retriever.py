# Target 응답 생성에 넣을 persona 글을 고르는 검색기
#
# 검색기 교체 구조
#   Retriever(Protocol)만 지키면 어떤 구현이든 responder에 꽂을 수 있다.
#   지금은 PersonaScopeRetriever 하나만 있고, 임베딩 기반 top-k 검색기는 팀 결정 후 추가한다.
#
# PersonaScopeRetriever가 query를 쓰지 않는 이유
#   SynthPAI comments는 전부 영어이고 Seed 질문은 전부 한국어다 (한글 포함 comment 0/7,823).
#   단어 일치 방식 검색은 겹치는 단어가 없어 순위를 매기지 못하고, 사실상 무작위로 잘라낸다.
#   persona 한 명의 글은 중앙값 16개·약 1,700자라 전부 넣어도 문맥 부담이 작다.
#   그래서 POC 기본값은 "해당 persona의 글 전체(필요하면 글자 수 상한)"로 둔다.
#   PoC(Synthea)는 다국어 임베딩 top-4를 썼다. 어느 쪽으로 갈지는 공개 가능한 단서 양을 바꾸는
#   연구 설정이라 Pilot 전에 팀이 정하고 retriever_name/version으로 기록한다.

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from target_llm.corpus import PersonaComment, PersonaCorpus


class ContaminationError(RuntimeError):
    """검색 결과에 요청한 target이 아닌 persona의 글이 섞였을 때. 실험을 즉시 멈춰야 하는 오류다."""


@dataclass(frozen=True)
class RetrievalResult:
    """
    target_id
    → 검색 범위로 지정한 persona
    comments
    → Target 문맥에 실제로 들어가는 글 (모두 target_id 소유)
    candidate_count
    → 범위 안에 있던 전체 글 수 (상한 때문에 몇 개가 빠졌는지 보기 위함)
    context_chars
    → 문맥에 들어간 글자 수 합계
    context_truncated
    → 상한 때문에 일부 글이 빠졌는지
    retriever_name / retriever_version
    → 어떤 검색 설정으로 돌렸는지 (run 로그 기록용)
    """
    target_id: str
    comments: tuple[PersonaComment, ...]
    candidate_count: int
    context_chars: int
    context_truncated: bool
    retriever_name: str
    retriever_version: str

    @property
    def comment_ids(self) -> tuple[str, ...]:
        """로그에는 본문 대신 id만 남긴다 (trusted 로그도 최소한으로)."""
        return tuple(comment.comment_id for comment in self.comments)


class Retriever(Protocol):
    """responder가 기대하는 검색기 인터페이스."""

    name: str
    version: str

    def retrieve(self, target_id: str, query: str) -> RetrievalResult:
        ...


class PersonaScopeRetriever:
    """
    지정한 persona의 글만, 원본 순서대로, 글자 수 상한 안에서 반환한다.

    max_context_chars가 None이면 상한 없이 전부 반환한다 (POC 기본값).
    상한이 있으면 글을 중간에 자르지 않고 통째로 넣다가 다음 글이 안 들어가는 지점에서 멈춘다.
    잘린 글 조각은 문장 뜻이 바뀌어 엉뚱한 단서를 만들 수 있기 때문이다.
    """

    name = "persona_scope"
    version = "1.0"

    def __init__(self, corpus: PersonaCorpus, *, max_context_chars: int | None = None) -> None:
        if max_context_chars is not None and max_context_chars <= 0:
            raise ValueError("max_context_chars는 1 이상이거나 None이어야 합니다.")
        self._corpus = corpus
        self._max_context_chars = max_context_chars

    def retrieve(self, target_id: str, query: str) -> RetrievalResult:
        """
        input : target_id (검색 범위 persona), query (인터페이스 호환용, 이 구현에서는 순위에 쓰지 않음)
        output : RetrievalResult
        """
        candidates = self._corpus.comments_for(target_id)

        selected: list[PersonaComment] = []
        used_chars = 0
        for comment in candidates:
            length = len(comment.text)
            if self._max_context_chars is not None and used_chars + length > self._max_context_chars:
                break
            selected.append(comment)
            used_chars += length

        # strict target filter 불변식. corpus가 이미 persona별로 나눠 두었지만,
        # 나중에 검색기를 교체해도 이 검사는 남겨 둔다 (공유 index를 쓰는 구현에서 실수 방지).
        foreign = [c.comment_id for c in selected if c.target_id != target_id]
        if foreign:
            raise ContaminationError(
                f"{target_id} 검색 결과에 다른 persona 글이 섞였습니다: {foreign[:5]}"
            )

        return RetrievalResult(
            target_id=target_id,
            comments=tuple(selected),
            candidate_count=len(candidates),
            context_chars=used_chars,
            context_truncated=len(selected) < len(candidates),
            retriever_name=self.name,
            retriever_version=self.version,
        )
