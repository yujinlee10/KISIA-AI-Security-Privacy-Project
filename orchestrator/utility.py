# 유용성(utility) 지표: Defender가 응답을 고친 대가를 잰다 (LLM 호출 없음)
#
# 9월 PoC 보고서 실험 5에 대응한다. PoC는 방어 전후 응답의 임베딩 유사도와 길이 비율을 쟀고,
# 무관한 응답 쌍의 유사도를 바닥값으로 두어 상대 비교만 했다. 여기서도 같은 틀을 쓰되,
# 임베딩 모델 없이 계산되는 글자 3-gram 코사인 유사도를 쓴다.
#
# 턴마다 재는 것
#   similarity     : 원 응답과 공개 응답의 글자 3-gram 코사인 유사도 (0~1). 그대로 공개했으면 1.0
#   length_ratio   : 공개 응답 길이 / 원 응답 길이
#   qi_retention   : 원 응답에서 표준 값으로 풀린 QI 종류 가운데, 공개 응답에도 (같은 값이든 넓힌 값이든) 남은 비율.
#                    원 응답에 풀린 QI가 없으면 None
#
# evaluation 로그의 utility_score 컬럼에는 similarity를 넣는다.
#
# 읽을 때 주의
#   · 글자 겹침을 재는 것이지 뜻이 보존됐는지를 재는 것이 아니다. 같은 뜻을 다른 말로 쓰면 낮게, 뜻이 달라져도
#     글자가 비슷하면 높게 나온다. "답변이 여전히 쓸모 있는가"의 절대 지표가 아니다 (PoC 보고서 §8.5와 같은 한계)
#   · 그래서 바닥값(서로 다른 질문에 대한 원 응답끼리의 유사도)과 함께 본다. 바닥값보다 낮으면 그 응답은
#     원래 답과의 관련이 무관한 답 수준으로 떨어진 것이다
#   · qi_retention이 낮은 것은 Defender의 목적(정보를 덜 내보내기)과 같은 방향이다. 낮을수록 나쁘다는 뜻이 아니라
#     "보호를 위해 얼마나 덜어냈는가"를 보여 주는 값이다

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

NGRAM = 3
_WHITESPACE = re.compile(r"\s+")


def _grams(text: str) -> Counter:
    cleaned = _WHITESPACE.sub(" ", text.strip().lower())
    if len(cleaned) < NGRAM:
        return Counter([cleaned]) if cleaned else Counter()
    return Counter(cleaned[i:i + NGRAM] for i in range(len(cleaned) - NGRAM + 1))


def similarity(a: str, b: str) -> float:
    """
    input : 두 문자열
    output : 글자 3-gram 코사인 유사도 (0~1, 소수 넷째 자리). 둘 다 비었으면 1.0, 한쪽만 비었으면 0.0
    """
    if a.strip() == b.strip():
        return 1.0
    left, right = _grams(a), _grams(b)
    if not left or not right:
        return 0.0
    dot = sum(count * right[gram] for gram, count in left.items())
    norm = math.sqrt(sum(c * c for c in left.values())) * math.sqrt(sum(c * c for c in right.values()))
    return round(min(1.0, dot / norm), 4) if norm else 0.0


@dataclass(frozen=True)
class TurnUtility:
    similarity: float
    length_ratio: float
    qi_retention: float | None

    def to_log_dict(self) -> dict[str, Any]:
        return {"similarity": self.similarity, "length_ratio": self.length_ratio, "qi_retention": self.qi_retention}


def turn_utility(raw_response: str, released_response: str,
                 raw_attributes: Iterable[str], released_attributes: Iterable[str]) -> TurnUtility:
    """
    input : raw_response (Target 원 응답), released_response (실제 공개한 응답),
            raw_attributes (원 응답에서 표준 값으로 풀린 관찰의 QI 코드들),
            released_attributes (공개 응답에서 표준 값으로 풀린 관찰의 QI 코드들)
    output : TurnUtility
    """
    before, after = set(raw_attributes), set(released_attributes)
    raw_length = len(raw_response.strip())
    return TurnUtility(
        similarity=similarity(raw_response, released_response),
        length_ratio=round(len(released_response.strip()) / raw_length, 4) if raw_length else 1.0,
        qi_retention=round(len(before & after) / len(before), 4) if before else None,
    )


def similarity_floor(responses: Sequence[str], *, max_pairs: int = 400) -> float | None:
    """
    input : responses (서로 다른 질문에 대한 원 응답들), max_pairs (계산할 쌍의 상한)
    output : 서로 다른 응답 쌍의 평균 유사도 (바닥값). 응답이 둘 미만이면 None

    쌍은 정해진 간격으로 고른다 (난수를 쓰지 않아 다시 계산해도 같은 값이 나온다).
    """
    unique = list(dict.fromkeys(text for text in responses if text.strip()))
    if len(unique) < 2:
        return None
    pairs = [(i, j) for i in range(len(unique)) for j in range(i + 1, len(unique))]
    step = max(1, len(pairs) // max_pairs)
    chosen = pairs[::step][:max_pairs]
    return round(sum(similarity(unique[i], unique[j]) for i, j in chosen) / len(chosen), 4)


def mean(values: Iterable[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else None
