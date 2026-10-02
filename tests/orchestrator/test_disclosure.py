# orchestrator/disclosure.py 테스트 — 공개 기록, State ID, QI 관계

from __future__ import annotations

import pytest

from normalizer.normalizer import MappingReason, MappingStatus, NormalizedObservation
from orchestrator.disclosure import EMPTY_STATE_ID, DisclosureLedger, qi_relation, state_id


def obs(attribute: str, canonical: str | None, *, oid: str = "t1-o1", raw: str = "원문",
        en: str | None = "english") -> NormalizedObservation:
    status = MappingStatus.MAPPED if canonical else MappingStatus.UNMAPPED
    reason = MappingReason.EXACT_MATCH if canonical else MappingReason.OUT_OF_RISK_SCOPE
    return NormalizedObservation(observation_id=oid, attribute=attribute, raw_value=raw, value_en=en,
                                 confidence=5, source_turn=1, status=status, reason=reason,
                                 canonical_value=canonical)


# ── 공개 기록 ─────────────────────────────────────────────

def test_view_merges_committed_and_uncommitted_in_qi_order():
    ledger = DisclosureLedger()
    ledger.record([obs("SEX", None, oid="t1-o1", en="female"), obs("LOC", "Switzerland", oid="t1-o2")],
                  committed_ids=["t1-o2"])

    view = ledger.view({"LOC": "Switzerland"})
    assert view == {"SEX": "female", "LOC": "Switzerland"}
    assert list(view) == ["SEX", "LOC"]            # core.schemas.QI_TYPES 순서


def test_committed_value_wins_over_uncommitted():
    ledger = DisclosureLedger()
    ledger.record([obs("OCCP", None, en="developer")], committed_ids=[])      # 정규화 실패로 commit 안 됨
    assert ledger.view({})["OCCP"] == "developer"
    assert ledger.view({"OCCP": "software engineer"})["OCCP"] == "software engineer"


def test_uncommitted_value_falls_back_to_raw_and_keeps_first():
    ledger = DisclosureLedger()
    ledger.record([obs("INC", None, raw="고소득", en=None)], committed_ids=[])
    ledger.record([obs("INC", None, raw="중간 소득", en=None)], committed_ids=[])
    assert ledger.view({}) == {"INC": "고소득"}


# ── State ID ──────────────────────────────────────────────

def test_state_id_is_stable_and_order_independent():
    assert state_id({}) == EMPTY_STATE_ID
    assert state_id({"LOC": "Switzerland", "AGE": "30s"}) == state_id({"AGE": "30s", "LOC": "Switzerland"})
    assert state_id({"LOC": "Switzerland"}) != state_id({"LOC": "Zurich, Switzerland"})


# ── QI 관계 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "before, after, released, expected",
    [
        ({}, {"LOC": "Switzerland"}, [], "NEW"),
        ({}, {}, [], "SAME"),                                                             # 아무것도 못 얻음
        ({"LOC": "Switzerland"}, {"LOC": "Zurich, Switzerland"}, [], "DESCENDANT"),
        ({"LOC": "Switzerland"}, {"LOC": "Switzerland"}, [obs("LOC", "Switzerland")], "SAME"),
        ({"LOC": "Zurich, Switzerland"}, {"LOC": "Zurich, Switzerland"}, [obs("LOC", "Switzerland")], "ANCESTOR"),
        ({"OCCP": "nurse"}, {"OCCP": "nurse"}, [obs("OCCP", "chef")], "INCOMPATIBLE"),
        ({"LOC": "Switzerland"}, {"LOC": "Switzerland"}, [obs("AGE", "30s")], "SAME"),    # 다른 QI만 나옴
    ],
)
def test_qi_relation(before, after, released, expected):
    assert qi_relation("LOC" if "OCCP" not in before else "OCCP", before, after, released) == expected
