# attack/registry.py 테스트 — 실제 O1 v0.4·O2 v0.3 파일 적재와 형식 오류

from __future__ import annotations

import json

import pytest

from attack.registry import (
    DEFAULT_REGISTRY_FILES,
    PLACEHOLDER_DISCLOSED,
    PLACEHOLDER_SUBJECT,
    RunMode,
    SeedGateError,
    SeedRegistry,
    SeedRegistryError,
)


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


# ── 실제 파일 (D1 §7.2 수치와 대조) ───────────────────────

def test_counts_match_d1(registry):
    assert len(registry) == 117
    assert registry.count_by("objective") == {"O1": 72, "O2": 45}
    assert registry.count_by("scenario_code") == {"S1": 13, "S2": 39, "S3": 26, "S4": 39}


def test_o2_covers_only_qis_with_hierarchy(registry):
    o2_qis = {s.target_qi for s in registry.seeds if s.objective == "O2"}
    assert o2_qis == {"AGE", "LOC", "OCCP", "EDU", "POB"}


def test_legacy_placeholder_is_renamed(registry):
    for seed in registry.seeds:
        assert "[CURRENT_QI_STATE]" not in seed.template_text
    o2 = [s for s in registry.seeds if s.objective == "O2"]
    assert all(PLACEHOLDER_DISCLOSED in s.placeholders for s in o2)


def test_o1_never_uses_disclosed_value(registry):
    """O1은 아직 모르는 정보를 묻는 질문이라 이전 공개값을 쓰지 않는다 (팀 합의)."""
    assert not any(s.needs_disclosed_value for s in registry.seeds if s.objective == "O1")


def test_missing_subject_rows_are_recorded_not_rejected(registry):
    missing = sorted(s.seed_id for s in registry.seeds if PLACEHOLDER_SUBJECT in s.declared_but_unused)
    assert missing == ["AGE-S2-O1-003", "EDU-S2-O1-002", "LOC-S2-O1-003", "OCCP-S2-O1-002", "SEX-S2-O1-003"]


def test_edu_ontology_note_is_separated(registry):
    seed = registry.by_id("EDU-S1-O2-001")
    assert seed.ontology_note == "current Core ontology: typically TERTIARY_EDUCATION"


def test_sources_record_version_and_hash(registry):
    o1, o2 = registry.sources
    assert (o1.seed_count, o1.corpus_version) == (72, "v0.4")
    assert (o2.seed_count, o2.corpus_version) == (45, None)
    assert len(o1.sha256) == 64


def test_development_mode_flags(registry):
    assert registry.mode is RunMode.DEVELOPMENT
    assert all(not s.enabled_for_main and not s.human_reviewed for s in registry.seeds)


def test_main_mode_refuses_unreviewed_seeds():
    with pytest.raises(SeedGateError):
        SeedRegistry.load(mode=RunMode.MAIN)


# ── 형식 오류 ──────────────────────────────────────────────

def _row(**overrides):
    row = {
        "seed_id": "X-1", "seed_version": "v0", "target_qi": "LOC",
        "attack_objective": "NEW_QI_ACQUISITION", "scenario_id": "S1_DIRECT",
        "template_text": "[SUBJECT]의 지역은?", "required_state_condition": "LOC not active",
        "allowed_placeholders": "[SUBJECT]", "expected_relation": "NEW",
        "human_reviewed": False, "development_only": True, "enabled_for_main": False,
    }
    row.update(overrides)
    return row


def _load_rows(tmp_path, rows, name="r.json"):
    path = tmp_path / name
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return SeedRegistry.load([path])


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_qi": "NAME"},
        {"attack_objective": "GUESS"},
        {"required_state_condition": "LOC sometimes"},
        {"template_text": "[SUBJECT] [SECRET]"},
        {"template_text": "[PREVIOUSLY_DISCLOSED_QI]", "allowed_placeholders": "[SUBJECT]"},
        {"scenario_id": ""},
    ],
)
def test_invalid_rows_are_rejected(tmp_path, overrides):
    with pytest.raises(SeedRegistryError):
        _load_rows(tmp_path, [_row(**overrides)])


def test_duplicate_seed_id_across_files(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps([_row()]), encoding="utf-8")
    b.write_text(json.dumps({"seeds": [_row()]}), encoding="utf-8")
    with pytest.raises(SeedRegistryError):
        SeedRegistry.load([a, b])


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SeedRegistry.load([tmp_path / "none.json"])


def test_default_files_exist():
    assert all(path.exists() for path in DEFAULT_REGISTRY_FILES)
