# validation/select_targets.py 테스트 — 사람 라벨로 층을 나누고 정해진 비율로 뽑기

from __future__ import annotations

import json

import pytest

from adapters.ontology_risk import build_population_index
from tests.orchestrator.sample_data import RAW_PROFILES, make_population
from validation.select_targets import RICH, SPARSE, Candidate, assess, select


def write_comments(path, labels):
    """labels: {대상: [{profile 필드: 사람 추정값}, …]} (글마다 하나)"""
    lines = []
    for author, profile in RAW_PROFILES.items():
        for review in labels.get(author, [{}]):
            human = {field: {"estimate": text, "hardness": 2, "certainty": 3} for field, text in review.items()}
            lines.append(json.dumps({"author": author, "profile": profile, "text": "…", "reviews": {"human": human}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def index():
    return build_population_index(make_population())


def test_correct_labels_narrow_the_candidate_set(tmp_path, index):
    path = write_comments(tmp_path / "c.jsonl", {
        "p1": [{"city_country": "Zurich, Switzerland"}, {"occupation": "software engineer", "age": "34"}],
    })
    p1 = next(c for c in assess(path, index, k_req=4) if c.target_id == "p1")
    assert p1.labelled_qis == ("AGE", "LOC", "OCCP") and p1.k_reachable == 1 and p1.stratum == RICH
    assert p1.comments == 2


def test_wrong_labels_are_ignored_and_broad_ones_count_as_broad(tmp_path, index):
    path = write_comments(tmp_path / "c.jsonl", {
        "p1": [{"city_country": "Berlin, Germany", "age": "30s"}],       # 도시는 틀렸고 나이는 넓게 맞았다
    })
    p1 = next(c for c in assess(path, index, k_req=4) if c.target_id == "p1")
    assert p1.labelled_qis == ("AGE",) and p1.k_reachable == 5 and p1.stratum == SPARSE


def test_target_without_labels_is_sparse(tmp_path, index):
    path = write_comments(tmp_path / "c.jsonl", {})
    assert all(c.k_reachable == 8 and c.stratum == SPARSE and c.labelled_qis == () for c in assess(path, index, 4))


POOL = [Candidate(f"pers{i}", 10, ("AGE",), 1 if i <= 6 else 500, RICH if i <= 6 else SPARSE) for i in range(1, 21)]


def test_selection_follows_the_share_and_is_reproducible():
    chosen = select(POOL, 10, rich_share=0.5, seed=7)
    assert [c.stratum for c in chosen] == [RICH] * 5 + [SPARSE] * 5
    assert chosen == select(POOL, 10, rich_share=0.5, seed=7)
    assert chosen != select(POOL, 10, rich_share=0.5, seed=8)


def test_included_targets_count_toward_their_stratum():
    chosen = select(POOL, 4, rich_share=0.5, seed=1, include=["pers1", "pers20"])
    assert {"pers1", "pers20"} <= {c.target_id for c in chosen}
    assert sum(c.stratum == RICH for c in chosen) == 2


def test_short_stratum_is_filled_from_the_other():
    chosen = select(POOL, 10, rich_share=0.9, seed=1)             # rich는 6명뿐인데 9명을 요구
    assert sum(c.stratum == RICH for c in chosen) == 6 and len(chosen) == 10


@pytest.mark.parametrize("kwargs", [{"count": 25}, {"count": 3, "rich_share": 1.5},
                                    {"count": 3, "include": ["nobody"]}, {"count": 1, "include": ["pers1", "pers2"]}])
def test_invalid_selection_requests(kwargs):
    with pytest.raises(ValueError):
        select(POOL, kwargs.pop("count"), rich_share=kwargs.pop("rich_share", 0.5), seed=1, **kwargs)


def test_selection_file_fields_do_not_include_ground_truth():
    record = POOL[0].to_log_dict()
    assert set(record) == {"target_id", "comments", "labelled_qis", "k_reachable", "stratum"}
