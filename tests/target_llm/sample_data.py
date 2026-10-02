# target_llm 테스트용 표본 데이터
#
# 원본 synthpai.jsonl과 같은 모양의 레코드를 만든다.
# profile·reviews·guesses·username에는 눈에 띄는 표식 문자열(GT_MARKER)을 넣어,
# 로더를 거친 뒤 어디에도 그 표식이 남지 않는지(GT 비유출) 검사할 수 있게 한다.

from __future__ import annotations

GT_MARKER = "GT_MARKER_must_never_leak"


def make_record(author: str, comment_id: str, text: str) -> dict:
    return {
        "author": author,
        "username": f"{GT_MARKER}_username",
        "profile": {"age": 33, "city_country": f"{GT_MARKER}_city", "occupation": f"{GT_MARKER}_job"},
        "text": text,
        "guesses": [f"{GT_MARKER}_guess"],
        "reviews": {"human": {"age": {"estimate": f"{GT_MARKER}_review"}}},
        "id": comment_id,
        "parent_id": "p0",
        "thread_id": "t0",
        "children": [],
    }


SAMPLE_RECORDS = [
    make_record("pers1", "c1", "I walk my dog along the river every morning."),
    make_record("pers2", "c2", "Night shifts at the hospital are exhausting."),
    make_record("pers1", "c3", "Just finished grading papers for my students."),
    make_record("pers1", "c4", "Thinking about renovating the kitchen this summer."),
    make_record("pers2", "c5", "Our team won the local bowling league!"),
]
