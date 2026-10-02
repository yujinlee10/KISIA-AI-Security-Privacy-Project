# normalizer·adapters 테스트용 표본
#
# 실제 SynthPAI 기준 집단에서 확인된 표기 문제를 일부러 넣었다.
#   - 국가 표기 갈림: "New York, United States" / "New York, USA"
#   - 같은 도시 이름이 다른 나라에 있음: "Paris, France" / "Paris, United States"
#   - REL 표기 갈림: "in a relationship" / "in relationship"

from __future__ import annotations

from dataclasses import dataclass

from core.schemas import InferenceMode
from qi_extractor.schema import EvidenceSpan, RawObservation


@dataclass(frozen=True)
class FakeRecord:
    """ReferenceVocabulary는 `.qis`만 본다."""
    qis: dict


SAMPLE_RECORDS = [
    FakeRecord({"AGE": "30s", "LOC": "Zurich, Switzerland", "OCCP": "software engineer", "EDU": "Master's", "REL": "single"}),
    FakeRecord({"AGE": "30s", "LOC": "Geneva, Switzerland", "OCCP": "software engineer", "EDU": "College Degree", "REL": "married"}),
    FakeRecord({"AGE": "40s", "LOC": "New York, United States", "OCCP": "nurse", "EDU": "PhD", "REL": "in a relationship"}),
    FakeRecord({"AGE": "40s", "LOC": "New York, United States", "OCCP": "nurse", "EDU": "HS Diploma", "REL": "in a relationship"}),
    FakeRecord({"AGE": "20s", "LOC": "New York, USA", "OCCP": "barista", "EDU": "College Degree", "REL": "in relationship"}),
    FakeRecord({"AGE": "50s", "LOC": "Paris, France", "OCCP": "chef", "EDU": "College Degree", "REL": "divorced"}),
    FakeRecord({"AGE": "teens", "LOC": "Paris, United States", "OCCP": "student", "EDU": "HS Diploma", "REL": "single"}),
]


def raw(attribute: str, raw_value: str, value_en: str | None = None, *,
        confidence: int = 5, observation_id: str = "t1-o1") -> RawObservation:
    """RawObservation을 짧게 만드는 도우미."""
    return RawObservation(
        observation_id=observation_id,
        attribute=attribute,
        raw_value=raw_value,
        value_en=value_en,
        inference_mode=InferenceMode.EXPLICIT,
        confidence=confidence,
        evidence_spans=(EvidenceSpan(text=raw_value, start=0, end=len(raw_value)),),
        source_turn=1,
    )
