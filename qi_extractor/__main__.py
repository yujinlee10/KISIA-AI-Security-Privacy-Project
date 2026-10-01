# Target → Extractor 연결 확인용: python -m qi_extractor --target pers1 "질문"
#
# Target을 1회 호출해 응답을 받고, 그 응답에서 QI를 추출해 출력한다 (LLM 호출 2회).
# 여기서는 Defender가 없으므로 raw candidate를 그대로 공개된 응답으로 간주한다 (C0 조건과 같음).
# 실험 로그가 아니라 연결과 추출 품질을 눈으로 확인하는 용도다.

from __future__ import annotations

import argparse

from core.llm_client import build_client
from qi_extractor.extractor import ExtractionError, QIExtractor
from target_llm.corpus import PersonaCorpus
from target_llm.responder import TargetResponder
from target_llm.retriever import PersonaScopeRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Target 응답에서 QI 추출 확인")
    parser.add_argument("query", help="Target에 보낼 질문")
    parser.add_argument("--target", default="pers1", help="대상 persona (기본 pers1)")
    args = parser.parse_args()

    corpus = PersonaCorpus.load()

    with build_client() as client:
        target = TargetResponder(PersonaScopeRetriever(corpus), client)
        extractor = QIExtractor(client)

        output = target.respond(args.target, args.query)
        print("------ Target 응답 (C0: 그대로 공개로 간주) ------")
        print(output.raw_candidate_response)

        try:
            result = extractor.extract(
                args.query,
                output.raw_candidate_response,
                source_turn=1,
                response_truncated=output.response_truncated,
            )
        except ExtractionError as exc:
            print("------ 추출 실패 ------")
            print(exc)
            print("LLM 출력 원문:", exc.raw_output[:500])
            return

    print(f"------ 추출 결과: 통과 {len(result.observations)}건 / 거부 {len(result.rejected)}건 "
          f"/ 시도 {result.attempts}회 / 토큰 {result.input_tokens}→{result.output_tokens} ------")
    for obs in result.observations:
        print(f"[{obs.attribute}] {obs.raw_value} (en: {obs.value_en}) "
              f"/ {obs.inference_mode.value} / conf {obs.confidence}")
        for span in obs.evidence_spans:
            print(f"    근거: \"{span.text}\"")
    for rej in result.rejected:
        print(f"[거부: {rej.reason.value}] {rej.detail}")
    print("------")


if __name__ == "__main__":
    main()
