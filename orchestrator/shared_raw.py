# 갈래 간 원 응답 공유 (paired_questions 실행에서 쓴다)
#
# Target은 대화 기록을 받지 않고 (대상, 질문)만으로 답한다. 그러니 같은 대상에게 같은 질문을 던지는 갈래들은
# 같은 원 응답(raw candidate)을 받아야 맞다. 그런데 실제 모델은 temperature 0에서도 호출마다 응답이 달라졌다
# (10/06 실행: 같은 질문 12개 중 8개에서 갈래 간 원 응답이 달랐다). 그 흔들림이 갈래 간 차이로 섞여 들어간다.
#
# 그래서 같은 (대상, 질문)의 원 응답과, 같은 원 응답의 추출 결과를 한 번만 만들어 갈래들이 함께 쓰게 한다.
# 이렇게 하면 갈래 간 차이는 공개 게이트(Defender 판정 → 재작성 → 재추출)에서만 생긴다.
#
# 다시 쓴 결과는 토큰·응답 시간·시도 횟수를 0으로 바꿔 돌려준다 (비용을 두 번 세지 않기 위함. 본문은 같다).
# 재작성한 응답은 문장이 달라 여기에 걸리지 않고 매번 새로 추출된다.

from __future__ import annotations

from dataclasses import replace
from typing import Any

from qi_extractor.schema import ExtractionResult
from target_llm.responder import TargetOutput


class SharedTarget:
    """TargetResponder와 같은 방법(respond)을 갖는다. 같은 (대상, 질문)이면 처음 받은 응답을 다시 준다."""

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self._cache: dict[tuple[Any, ...], TargetOutput] = {}

    def respond(self, target_id: str, query: str, **kwargs: Any) -> TargetOutput:
        key = (target_id, query, tuple(kwargs.get("rule_constraints") or ()))
        cached = self._cache.get(key)
        if cached is not None:
            return replace(cached, input_tokens=0, output_tokens=0, latency_ms=0.0, attempts=0)
        output = self._responder.respond(target_id, query, **kwargs)
        self._cache[key] = output
        return output


class SharedExtractor:
    """QIExtractor와 같은 방법(extract)을 갖는다. 같은 (질문, 응답, 턴)이면 처음 추출한 결과를 다시 준다."""

    def __init__(self, extractor: Any) -> None:
        self._extractor = extractor
        self._cache: dict[tuple[str, str, int, bool], ExtractionResult] = {}

    def extract(self, query: str, response: str, *, source_turn: int,
                response_truncated: bool = False) -> ExtractionResult:
        key = (query, response, source_turn, response_truncated)
        cached = self._cache.get(key)
        if cached is not None:
            return replace(cached, input_tokens=0, output_tokens=0, latency_ms=0.0, attempts=0)
        result = self._extractor.extract(query, response, source_turn=source_turn,
                                         response_truncated=response_truncated)
        self._cache[key] = result
        return result
