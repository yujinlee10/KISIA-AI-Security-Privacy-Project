# 근거 문구(evidence span) 검증
#
# 1) locate_span : LLM이 준 근거 문구가 응답 원문 어디에 있는지 찾는다.
#    D1 Stage 2는 "응답 원문의 exact span"을 요구한다. 다만 LLM이 공백·줄바꿈을 바꾸거나
#    앞뒤에 따옴표를 붙이는 일이 흔해서, 그 두 가지만 허용하고 글자는 한 자도 다르면 안 된다.
#    찾으면 원문에서 잘라낸 문자열과 위치를 돌려준다 (LLM이 준 문자열을 그대로 믿지 않음).
#
# 2) is_query_echo : 근거가 전부 질문 문구를 되풀이한 것인지 본다.
#    D1: "Query는 해석 문맥이며, 질문에만 나온 값은 공개 정보로 인정하지 않는다."
#    예) 질문 "30대인가요?" / 응답 "30대인지는 알 수 없습니다" / 근거 "30대"
#        → 근거가 질문 안에 그대로 있으므로 응답이 새로 공개한 것이 아니다.
#    근거 중 하나라도 질문에 없는 구절이면 통과시킨다.
#    ("네, 서울에 삽니다"처럼 응답이 값을 다시 말하며 확인한 경우는 공개로 본다.)

from __future__ import annotations

import re
import unicodedata

# LLM이 근거 앞뒤에 붙이곤 하는 따옴표·괄호
_QUOTE_CHARS = "\"'“”‘’「」『』`"
_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """
    input : text
    output : NFC 정규화한 text

    맥 파일명·일부 LLM 출력의 한글이 자모 분리(NFD) 형태로 오면 같은 글자도 비교가 어긋난다.
    응답·질문·근거를 모두 NFC로 맞춘 뒤 비교한다.
    """
    return unicodedata.normalize("NFC", text)


def locate_span(span: str, response: str) -> tuple[int, int] | None:
    """
    input : span (LLM이 준 근거 문구), response (NFC 정규화된 응답 원문)
    output : (start, end) 또는 None (원문에 없음)

    1단계: 그대로 찾기
    2단계: 공백 차이만 허용해서 찾기 (단어 사이 공백 개수·줄바꿈 차이)
    """
    cleaned = normalize_text(span).strip().strip(_QUOTE_CHARS).strip()
    if not cleaned:
        return None

    index = response.find(cleaned)
    if index >= 0:
        return index, index + len(cleaned)

    tokens = cleaned.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    match = re.search(pattern, response)
    if match:
        return match.start(), match.end()
    return None


def is_query_echo(span_texts: list[str], query: str) -> bool:
    """
    input : span_texts (응답에서 찾은 근거 문자열들), query (NFC 정규화된 질문)
    output : True면 근거가 전부 질문 안에 이미 있던 문구

    공백 차이는 무시하고 비교한다.
    """
    if not span_texts:
        return False
    squashed_query = _squash(query)
    return all(_squash(text) in squashed_query for text in span_texts)


def _squash(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()
