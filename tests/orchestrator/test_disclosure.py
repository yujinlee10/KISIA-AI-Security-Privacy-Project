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

def compare(attribute, existing, candidate):
    """risk_defender 백엔드와 같은 판단: 도시가 국가보다 구체적이다."""
    if existing == candidate:
        return "SAME"
    if "," in str(existing) and "," not in str(candidate):
        return "ANCESTOR"
    if "," in str(candidate) and "," not in str(existing):
        return "DESCENDANT"
    return "INCOMPATIBLE"


@pytest.mark.parametrize(
    "target, before, after, released, expected",
    [
        ("LOC", {}, {"LOC": "Switzerland"}, [], "NEW"),
        ("LOC", {}, {}, [], "SAME"),                                                             # 아무것도 못 얻음
        ("LOC", {"LOC": "Switzerland"}, {"LOC": "Zurich, Switzerland"}, [], "DESCENDANT"),
        ("LOC", {"LOC": "Switzerland"}, {"LOC": "Switzerland"}, [obs("LOC", "Switzerland")], "SAME"),
        ("LOC", {"LOC": "Zurich, Switzerland"}, {"LOC": "Zurich, Switzerland"}, [obs("LOC", "Switzerland")], "ANCESTOR"),
        ("OCCP", {"OCCP": "nurse"}, {"OCCP": "nurse"}, [obs("OCCP", "chef")], "INCOMPATIBLE"),
        ("LOC", {"LOC": "Switzerland"}, {"LOC": "Switzerland"}, [obs("AGE", "30s")], "SAME"),    # 다른 QI만 나옴
    ],
)
def test_qi_relation(target, before, after, released, expected):
    assert qi_relation(target, before, after, before, after, released, compare) == expected


def test_first_uncommitted_disclosure_is_new_and_later_commit_is_new_for_state():
    """State에 못 들어간 공개(정규화 실패 등)도 처음이면 NEW다. 다음에 그 QI가 State에 들어가면 State 기준으로 NEW다."""
    loose = {"LOC": "Busan"}                       # 공개는 됐지만 State에는 없음
    assert qi_relation("LOC", {}, {}, {}, loose, [], compare) == "NEW"
    assert qi_relation("LOC", {}, {}, loose, loose, [], compare) == "SAME"
    assert qi_relation("LOC", {}, {"LOC": "Switzerland"}, loose, {"LOC": "Switzerland"}, [], compare) == "NEW"
