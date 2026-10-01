# Target 단독 실행 확인용: python -m target_llm --target pers1 "질문"
#
# 실제 monogpt를 1회 호출해 raw_candidate_response를 출력한다.
# 출력되는 응답은 "공개 전 초안"이다. 연결·문맥 구성이 맞는지 눈으로 확인하는 용도이며 실험 로그가 아니다.

from __future__ import annotations

import argparse

from core.llm_client import build_client
from target_llm.corpus import PersonaCorpus
from target_llm.responder import TargetResponder
from target_llm.retriever import PersonaScopeRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Target System 단독 호출 확인")
    parser.add_argument("query", help="Target에 보낼 질문")
    parser.add_argument("--target", default="pers1", help="대상 persona (기본 pers1)")
    parser.add_argument("--max-context-chars", type=int, default=None, help="문맥 글자 수 상한 (기본 없음)")
    args = parser.parse_args()

    corpus = PersonaCorpus.load()
    print("------")
    print(f"corpus: persona {corpus.info.persona_count}명 / 글 {corpus.info.comment_count}개 "
          f"/ sha256 {corpus.info.sha256[:12]}…")

    retriever = PersonaScopeRetriever(corpus, max_context_chars=args.max_context_chars)

    with build_client() as client:
        responder = TargetResponder(retriever, client)
        output = responder.respond(args.target, args.query)

    log = output.to_log_dict()
    print(f"target: {output.target_id} / 문맥 글 {len(log['retrieved_comment_ids'])}개 "
          f"({log['retrieval_context_chars']}자, 잘림={log['retrieval_context_truncated']})")
    print(f"model: {output.response_model} / stop_reason: {output.stop_reason} "
          f"/ 토큰 {output.input_tokens}→{output.output_tokens} / {output.latency_ms:.0f}ms")
    print("------ raw_candidate_response (공개 전 초안) ------")
    print(output.raw_candidate_response)
    print("------")


if __name__ == "__main__":
    main()
