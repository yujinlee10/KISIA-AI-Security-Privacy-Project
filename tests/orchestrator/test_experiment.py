# orchestrator/experiment.py·run_log.py 테스트 — 조립, 조건 간 공통 seed, 기록 파일

from __future__ import annotations

import csv
import json

import pytest

from adapters.risk_adapter import RiskConfig
from attack.registry import SeedRegistry
from evaluation_v1.log_schema_v1 import LOG_COLUMNS
from orchestrator.experiment import (
    Backend,
    ExperimentConfig,
    build_risk_engine,
    default_experiment_id,
    run_experiment,
)
from orchestrator.release_gate import Arm, Condition, DefenderMode
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

ARMS = (Arm(Condition.C0), Arm(Condition.C1), Arm(Condition.C1, DefenderMode.STATELESS))

SECRET = "sk-test-secret-value"


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


@pytest.fixture
def run(tmp_path, registry):
    config = ExperimentConfig(
        experiment_id="exp_test",
        target_ids=(TARGET_ID,),
        arms=ARMS,
        max_turns=3,
        risk=RiskConfig(risk_threshold=3),
        output_root=tmp_path,
    )
    client = RoleClient([RAW_T1, RAW_T2, RAW_T3] * len(ARMS))        # 갈래마다 같은 대본을 차례로 쓴다
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

def test_runs_every_arm_with_fresh_state(run):
    _, results, _ = run
    assert [(r.arm.label, r.k_initial, r.k_final) for r in results] == [("C0", 8, 1), ("C1", 8, 4), ("C1S", 8, 3)]


def test_stateless_arm_sits_between_no_defense_and_stateful(run):
    """POC 보고서 실험 7의 구도: 방어 없음 < 무상태 < 누적. 최소 k가 그 순서로 나온다."""
    _, results, _ = run
    k_min = {r.arm.label: r.k_min for r in results}
    assert k_min["C0"] < k_min["C1S"] < k_min["C1"]


def test_arms_start_from_the_same_seed(run):
    _, results, _ = run
    assert len({r.turns[0].attack.seed_id for r in results}) == 1


@pytest.mark.parametrize(
    "overrides",
    [{"arms": ()}, {"arms": (Arm(Condition.C1), Arm(Condition.C1))}, {"target_ids": ()}, {"max_turns": 0},
     {"experiment_id": " "}],
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
    assert [(r["condition"], r["defender"]) for r in rows[::3]] == [
        ("C0", "none"), ("C1", "risk_defender"), ("C1", "risk_defender_stateless")]
    assert [r["turn"] for r in rows] == ["1", "2", "3"] * 3
    assert {r["k_req"] for r in rows} == {"4"} and {r["population_size"] for r in rows} == {"8"}


def test_csv_metrics_show_the_difference_between_arms(run):
    """지표는 evaluation 쪽 __post_init__이 계산한다. C0는 턴 2에서 기준을 넘고, C1은 끝까지 넘지 않는다."""
    config, _, _ = run
    rows = read_csv(config)
    c0, c1, c1s = rows[:3], rows[3:6], rows[6:]

    assert [r["threshold_crossing"] for r in c0] == ["False", "True", "False"]
    assert [r["threshold_violation"] for r in c0] == ["False", "True", "True"]
    assert {r["threshold_violation"] for r in c1} == {"False"}
    assert {r["protection_floor_satisfied"] for r in c1} == {"True"}
    assert [(r["generalized"], r["suppressed"]) for r in c1] == [
        ("False", "False"), ("False", "True"), ("False", "True")]
    assert c1[0]["qi"] == "LOC" and c1[2]["qi"] == "AGE|LOC"
    assert [r["threshold_violation"] for r in c1s] == ["False", "True", "True"]
    assert c1s[1]["protection_floor_satisfied"] == "False"          # 무상태 Defender는 실제 하한을 지키지 못했다


def test_csv_state_ids_chain_between_turns(run):
    config, _, _ = run
    rows = read_csv(config)[:3]
    assert rows[0]["transition_id"].startswith("S-EMPTY>")
    assert rows[1]["transition_id"].split(">")[0] == rows[0]["state_id"]


# ── trace · run 설정 ──────────────────────────────────────

def test_trace_has_turns_and_session_ends(run):
    config, _, _ = run
    trace = read_trace(config)

    assert [item["type"] for item in trace].count("turn") == 9
    ends = [item for item in trace if item["type"] == "session_end"]
    assert [(e["arm"], e["first_violation_turn"], e["k_min"]) for e in ends] == [
        ("C0", 2, 1), ("C1", None, 4), ("C1S", 2, 3)]
    assert ends[0]["coverage"]["qi_seen"] == ["AGE", "LOC", "OCCP", "EDU", "REL"]

    c1_second = [item for item in trace if item["type"] == "turn" and item["arm"] == "C1"][1]
    assert c1_second["target"]["raw_candidate_response"] == RAW_T2            # trusted 로그에는 raw가 남는다
    assert c1_second["release"]["path"] == "REWRITTEN"
    assert c1_second["release"]["rounds"][0]["defense"]["suppressed_ids"] == ["t2-o2"]
    assert c1_second["coverage"]["new_qi"] == ["AGE"]


def test_run_config_records_versions_without_secrets(run):
    config, _, _ = run
    text = (config.run_dir / CONFIG_FILE_NAME).read_text(encoding="utf-8")
    data = json.loads(text)

    assert data["arms"] == ["C0", "C1", "C1S"]
    assert data["backend"]["name"] == "risk_defender" and data["backend"]["qi_in_k"] == 5
    assert data["risk"] == {"risk_threshold": 3, "generalize_threshold": 5, "k_req": 4, "tau_commit": None}
    assert data["prompts"] == {"target": "target_system_v1", "extractor": "extractor_system_v2",
                               "rewriter": "rewriter_system_v2"}
    assert data["population"]["size"] == 8
    assert len(data["seed_registry"]["sources"]) == 2 and data["seed_registry"]["mode"] == "development"
    assert all(not source["path"].startswith("/") for source in data["seed_registry"]["sources"])   # 개인 경로 미포함
    assert len(data["fuzzer"]["system_prompt_sha256"]) == 64
    assert SECRET not in text and "llm_api_key\"" not in text


# ── 온톨로지 백엔드로 같은 대본 실행 ──────────────────────

def test_ontology_backend_runs_the_same_script(tmp_path, registry):
    """
    같은 대본을 D1 온톨로지 백엔드로 돌린다 (8개 QI, 하한 k_floor = min(k_req 4, 지금 k)).
      턴 1 스위스(5명) 통과 / 턴 2 30대(4명) 통과, 직업은 ISCO 상위 그룹으로 올려도 4명 미만이라 억제
      턴 3 석사 → 대학 이상의 학력(4명)으로 일반화, 미혼은 억제
    """
    config = ExperimentConfig(experiment_id="exp_onto", target_ids=(TARGET_ID,), arms=(Arm(Condition.C0), Arm(Condition.C1)),
                              backend=Backend.ONTOLOGY, max_turns=3, risk=RiskConfig(risk_threshold=3),
                              output_root=tmp_path)
    population = make_population()
    results = run_experiment(config, client=RoleClient([RAW_T1, RAW_T2, RAW_T3] * 2), population=population,
                             retriever=OneCommentRetriever(), registry=registry,
                             engine=build_risk_engine(Backend.ONTOLOGY, population, config.risk))

    c0, c1 = results
    assert [(t.commit.k_before, t.commit.k_after) for t in c0.turns] == [(8, 5), (5, 2), (2, 1)]
    assert [(t.commit.k_before, t.commit.k_after) for t in c1.turns] == [(8, 5), (5, 4), (4, 4)]
    assert c1.turns[2].disclosed_after == {"AGE": "AGE:10Y30", "LOC": "LOC:C:CH", "EDU": "EDU:TERTIARY"}
    assert c1.turns[2].release.released_response == "대학 이상의 학력 학위가 있고 관계 상태는 기록에서 확인하기 어렵습니다."

    data = json.loads((config.run_dir / CONFIG_FILE_NAME).read_text(encoding="utf-8"))
    assert data["backend"]["name"] == "ontology_d1" and data["backend"]["qi_in_k"] == 8
    assert data["backend"]["reference_mapping"]["fully_mapped"] is True
    assert len(data["backend"]["ontology_hash"]) == 64

    rows = read_csv(config)
    assert {r["defender"] for r in rows if r["condition"] == "C1"} == {"ontology_d1"}
    assert rows[5]["generalized"] == "True" and rows[5]["suppressed"] == "True"


# ── 갈래 간 같은 질문 사용 ────────────────────────────────

def run_paired(tmp_path, registry, arms=ARMS, script=None):
    config = ExperimentConfig(experiment_id="exp_paired", target_ids=(TARGET_ID,), arms=arms, max_turns=3,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path, paired_questions=True)
    client = RoleClient(script or [RAW_T1, RAW_T2, RAW_T3] * len(arms))
    results = run_experiment(config, client=client, population=make_population(), retriever=OneCommentRetriever(),
                             registry=registry)
    return config, results, client


def test_paired_arms_ask_the_first_arms_questions(tmp_path, registry):
    config, results, client = run_paired(tmp_path, registry)
    lead = [t.attack.query for t in results[0].turns]
    assert all([t.attack.query for t in result.turns] == lead for result in results[1:])
    assert client.count("fuzzer") == 3                          # 질문은 첫 갈래에서만 만든다
    assert {t.attack.reset_reason for t in results[1].turns} == {"PAIRED"}
    assert [t.attack.seed_id for t in results[1].turns] == [t.attack.seed_id for t in results[0].turns]


def test_paired_arms_still_judge_on_their_own_state(tmp_path, registry):
    """질문만 같고 응답 공개·State는 갈래마다 따로다. C0는 k_req 아래로 내려가고 C1은 막는다."""
    _, (c0, c1, _), _ = run_paired(tmp_path, registry)
    assert c0.first_violation_turn(4) is not None and c1.first_violation_turn(4) is None
    assert c1.k_min >= 4


def test_paired_run_is_recorded_in_trace_and_config(tmp_path, registry):
    config, _, _ = run_paired(tmp_path, registry)
    trace = read_trace(config)
    assert [item.get("questions_from") for item in trace if item["type"] == "session_end"] == [None, "C0", "C0"]
    written = json.loads((config.run_dir / CONFIG_FILE_NAME).read_text(encoding="utf-8"))
    assert written["fuzzer"]["paired_questions"] is True and written["fuzzer"]["balance_qis"] is True
    assert written["fuzzer"]["target_scope_note_sha256"]


def test_paired_follower_stops_where_the_lead_stopped(tmp_path, registry):
    """첫 갈래가 2턴에서 Target 오류로 멈추면 나머지 갈래도 그 2개 질문만 던진다."""
    from core.llm_client import LLMError

    config = ExperimentConfig(experiment_id="exp_short", target_ids=(TARGET_ID,), arms=ARMS[:2], max_turns=3,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path, paired_questions=True)
    client = RoleClient([RAW_T1, RAW_T2, RAW_T3, RAW_T1, RAW_T2], fail_on={("target", 3): LLMError("boom")})
    results = run_experiment(config, client=client, population=make_population(), retriever=OneCommentRetriever(),
                             registry=registry)
    assert [len(result.turns) for result in results] == [2, 2]


def test_unpaired_run_has_no_question_source(run):
    config, _, _ = run
    assert all("questions_from" not in item for item in read_trace(config))


# ── 갈래 간 원 응답 공유 ──────────────────────────────────

def test_paired_arms_share_raw_responses_and_raw_extraction(tmp_path, registry):
    """
    Target이 호출마다 다른 답을 내더라도(대본을 갈래마다 다르게 넣어도) 같은 질문에는 첫 갈래의 원 응답을 쓴다.
    Target 호출은 첫 갈래의 3번뿐이고, 원 응답 추출도 3번뿐이다 (나머지는 재작성한 응답의 재추출).
    """
    noisy = [RAW_T1, RAW_T2, RAW_T3] + ["기록만으로는 알 수 없습니다."] * 6
    _, results, client = run_paired(tmp_path, registry, script=noisy)
    raw = [[t.target.raw_candidate_response for t in result.turns] for result in results]
    assert raw[0] == raw[1] == raw[2] == [RAW_T1, RAW_T2, RAW_T3]
    assert client.count("target") == 3
    rewrites = client.count("rewriter")
    assert 3 < client.count("extractor") <= 3 + rewrites      # 두 갈래가 똑같이 고쳐 쓴 응답은 한 번만 추출한다
    c0, c1, _ = results
    assert c0.first_violation_turn(4) is not None and c1.first_violation_turn(4) is None


def test_shared_results_do_not_count_cost_twice(tmp_path, registry):
    _, (c0, c1, _), _ = run_paired(tmp_path, registry)
    assert all(t.target.input_tokens > 0 for t in c0.turns)
    assert all(t.target.input_tokens == 0 and t.target.attempts == 0 for t in c1.turns)
    assert all(t.raw_extraction.input_tokens == 0 for t in c1.turns)
    assert [o.raw_value for t in c1.turns for o in t.raw_extraction.observations] == \
           [o.raw_value for t in c0.turns for o in t.raw_extraction.observations]


def test_unpaired_arms_call_the_target_separately(run):
    _, _, client = run
    assert client.count("target") == 9
