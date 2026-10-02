# orchestrator/experiment.py·run_log.py 테스트 — 조립, 조건 간 공통 seed, 기록 파일

from __future__ import annotations

import csv
import json

import pytest

from adapters.risk_adapter import RiskConfig
from attack.registry import SeedRegistry
from evaluation_v1.log_schema_v1 import LOG_COLUMNS
from orchestrator.experiment import ExperimentConfig, default_experiment_id, run_experiment
from orchestrator.release_gate import Condition
from orchestrator.run_log import CONFIG_FILE_NAME, CSV_FILE_NAME, TRACE_FILE_NAME
from tests.orchestrator.sample_data import (
    RAW_T1,
    RAW_T2,
    RAW_T3,
    TARGET_ID,
    OneCommentRetriever,
    RoleClient,
    make_population,
)

SECRET = "sk-test-secret-value"


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


@pytest.fixture
def run(tmp_path, registry):
    config = ExperimentConfig(
        experiment_id="exp_test",
        target_ids=(TARGET_ID,),
        conditions=(Condition.C0, Condition.C1),
        max_turns=3,
        risk=RiskConfig(risk_threshold=3),
        output_root=tmp_path,
    )
    client = RoleClient([RAW_T1, RAW_T2, RAW_T3] * 2)        # 조건 두 개가 같은 대본을 차례로 쓴다
    results = run_experiment(
        config, client=client, population=make_population(), retriever=OneCommentRetriever(),
        registry=registry, settings_description={"llm_model": "fake-model", "llm_api_key_set": True},
    )
    return config, results, client


def read_csv(config):
    with (config.run_dir / CSV_FILE_NAME).open(encoding="utf-8") as file:
        return list(csv.DictReader(file))


def read_trace(config):
    lines = (config.run_dir / TRACE_FILE_NAME).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


# ── 조립 ──────────────────────────────────────────────────

def test_runs_every_condition_with_fresh_state(run):
    _, results, _ = run
    assert [(r.condition, r.k_initial, r.k_final) for r in results] == [(Condition.C0, 8, 1), (Condition.C1, 8, 4)]


def test_conditions_start_from_the_same_seed(run):
    _, results, _ = run
    assert results[0].turns[0].attack.seed_id == results[1].turns[0].attack.seed_id


@pytest.mark.parametrize(
    "overrides",
    [{"conditions": (Condition.C2,)}, {"conditions": ()}, {"target_ids": ()}, {"max_turns": 0}, {"experiment_id": " "}],
)
def test_invalid_config(overrides):
    base = {"experiment_id": "e", "target_ids": ("p1",)}
    with pytest.raises(ValueError):
        ExperimentConfig(**{**base, **overrides})


def test_default_experiment_id_format():
    assert default_experiment_id().startswith("poc_")


# ── evaluation CSV (이정민 파트 형식) ─────────────────────

def test_csv_follows_evaluation_schema(run):
    config, _, _ = run
    rows = read_csv(config)

    assert list(rows[0]) == LOG_COLUMNS
    assert [(r["condition"], r["turn"]) for r in rows] == [
        ("C0", "1"), ("C0", "2"), ("C0", "3"), ("C1", "1"), ("C1", "2"), ("C1", "3")]
    assert {r["defender"] for r in rows if r["condition"] == "C0"} == {"none"}
    assert {r["defender"] for r in rows if r["condition"] == "C1"} == {"risk_defender"}
    assert {r["k_req"] for r in rows} == {"4"} and {r["population_size"] for r in rows} == {"8"}


def test_csv_metrics_show_the_difference_between_conditions(run):
    """지표는 evaluation 쪽 __post_init__이 계산한다. C0는 턴 1에서 기준을 넘고, C1은 끝까지 넘지 않는다."""
    config, _, _ = run
    rows = read_csv(config)
    c0, c1 = rows[:3], rows[3:]

    assert [r["threshold_crossing"] for r in c0] == ["True", "False", "False"]
    assert [r["threshold_violation"] for r in c0] == ["True", "True", "True"]
    assert {r["threshold_violation"] for r in c1} == {"False"}
    assert {r["protection_floor_satisfied"] for r in c1} == {"True"}
    assert [(r["generalized"], r["suppressed"]) for r in c1] == [
        ("True", "False"), ("False", "True"), ("True", "True")]
    assert c1[0]["qi"] == "LOC" and c1[2]["qi"] == "AGE|LOC|EDU"


def test_csv_state_ids_chain_between_turns(run):
    config, _, _ = run
    rows = read_csv(config)[:3]
    assert rows[0]["transition_id"].startswith("S-EMPTY>")
    assert rows[1]["transition_id"].split(">")[0] == rows[0]["state_id"]


# ── trace · run 설정 ──────────────────────────────────────

def test_trace_has_turns_and_session_ends(run):
    config, _, _ = run
    trace = read_trace(config)

    assert [item["type"] for item in trace].count("turn") == 6
    ends = [item for item in trace if item["type"] == "session_end"]
    assert [(e["condition"], e["first_violation_turn"], e["k_min"]) for e in ends] == [("C0", 1, 1), ("C1", None, 4)]

    c1_first = next(item for item in trace if item["type"] == "turn" and item["condition"] == "C1")
    assert c1_first["target"]["raw_candidate_response"] == RAW_T1            # trusted 로그에는 raw가 남는다
    assert c1_first["release"]["path"] == "REWRITTEN"
    assert c1_first["release"]["rounds"][0]["defense"]["generalized"][0]["to"] == "Switzerland"


def test_run_config_records_versions_without_secrets(run):
    config, _, _ = run
    text = (config.run_dir / CONFIG_FILE_NAME).read_text(encoding="utf-8")
    data = json.loads(text)

    assert data["risk"] == {"risk_threshold": 3, "k_req": 4, "tau_commit": None}
    assert data["prompts"] == {"target": "target_system_v1", "extractor": "extractor_system_v1",
                               "rewriter": "rewriter_system_v1"}
    assert data["population"]["size"] == 8
    assert len(data["seed_registry"]["sources"]) == 2 and data["seed_registry"]["mode"] == "development"
    assert all(not source["path"].startswith("/") for source in data["seed_registry"]["sources"])   # 개인 경로 미포함
    assert len(data["fuzzer"]["system_prompt_sha256"]) == 64
    assert SECRET not in text and "llm_api_key\"" not in text
