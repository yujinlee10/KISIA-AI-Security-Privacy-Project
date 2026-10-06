# 위험 경로 보관, Exact Replay, 실행 집계, Seed 범위 제한 테스트 (가짜 LLM)

from __future__ import annotations

import csv
import json

import pytest

from adapters.risk_adapter import RISK_DEFENDER_QI_TYPES, RiskConfig
from attack.registry import SeedRegistry
from orchestrator.archive import ARCHIVE_FILE_NAME, UNVALIDATED, load_paths, path_id
from orchestrator.experiment import Backend, ExperimentConfig, build_risk_engine, run_experiment
from orchestrator.release_gate import Arm, Condition
from orchestrator.replay import REPLAY_SUFFIX, ReplayFuzzer, config_from_source, load_source_config, replay_paths
from orchestrator.report import REPORT_FILE_NAME, load_run, render
from orchestrator.run_log import CSV_FILE_NAME, TRACE_FILE_NAME
from tests.orchestrator.sample_data import (
    RAW_T1,
    RAW_T2,
    RAW_T3,
    TARGET_ID,
    OneCommentRetriever,
    RoleClient,
    make_population,
)

ARMS = (Arm(Condition.C0), Arm(Condition.C1))
SCRIPT = [RAW_T1, RAW_T2, RAW_T3]


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


def execute(tmp_path, registry, *, backend=Backend.RISK_DEFENDER, **overrides):
    config = ExperimentConfig(experiment_id="src", target_ids=(TARGET_ID,), arms=ARMS, backend=backend, max_turns=3,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path, **overrides)
    population = make_population()
    client = RoleClient(SCRIPT * len(ARMS))
    results = run_experiment(config, client=client, population=population, retriever=OneCommentRetriever(),
                             registry=registry, engine=build_risk_engine(backend, population, config.risk))
    return config, results, population


def read_trace(run_dir):
    return [json.loads(line) for line in (run_dir / TRACE_FILE_NAME).read_text(encoding="utf-8").splitlines()]


# ── Seed 범위 제한 ────────────────────────────────────────

def test_seeds_stay_within_qis_the_backend_counts(tmp_path, registry):
    """risk_defender는 5개 QI만 센다. 소득·성별·출생지를 묻는 Seed는 고르지 않는다."""
    _, results, _ = execute(tmp_path, registry)
    assert {t.attack.target_qi for r in results for t in r.turns} <= set(RISK_DEFENDER_QI_TYPES)


def test_scope_restriction_can_be_turned_off(tmp_path, registry):
    """제한을 끄면 이 난수 seed의 첫 질문은 소득(INC)을 노린다 (Part 7까지의 동작)."""
    _, results, _ = execute(tmp_path, registry, restrict_seeds_to_counted_qis=False)
    assert results[0].turns[0].attack.target_qi == "INC"


# ── 위험 경로 보관 ────────────────────────────────────────

def test_only_sessions_that_went_below_k_req_are_archived(tmp_path, registry):
    config, results, _ = execute(tmp_path, registry)
    c0, c1 = results
    assert c0.first_violation_turn(4) is not None and c1.first_violation_turn(4) is None
    (record,) = load_paths(config.run_dir)
    assert record["path_id"] == path_id("src", TARGET_ID, "C0")
    assert record["validation_status"] == UNVALIDATED
    assert record["first_violation_turn"] == c0.first_violation_turn(4)
    assert record["min_k"] == c0.k_min


def test_archived_record_keeps_queries_but_not_responses(tmp_path, registry):
    config, results, _ = execute(tmp_path, registry)
    (record,) = load_paths(config.run_dir)
    assert [q["query"] for q in record["queries"]] == [t.attack.query for t in results[0].turns]
    assert all(q["seed_id"] and q["target_qi"] for q in record["queries"])
    assert [t["k_after"] for t in record["turns"]] == [t.commit.k_after for t in results[0].turns]
    text = (config.run_dir / ARCHIVE_FILE_NAME).read_text(encoding="utf-8")
    assert not any(raw in text for raw in SCRIPT)


def test_session_end_points_to_the_archived_path(tmp_path, registry):
    config, _, _ = execute(tmp_path, registry)
    ends = [item for item in read_trace(config.run_dir) if item["type"] == "session_end"]
    assert [item["archived_path_id"] for item in ends] == [path_id("src", TARGET_ID, "C0"), None]


def test_no_archive_file_without_violation(tmp_path, registry):
    config = ExperimentConfig(experiment_id="safe", target_ids=(TARGET_ID,), arms=(Arm(Condition.C1),), max_turns=3,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path)
    run_experiment(config, client=RoleClient(SCRIPT), population=make_population(),
                   retriever=OneCommentRetriever(), registry=registry)
    assert load_paths(config.run_dir) == []
    assert not (config.run_dir / ARCHIVE_FILE_NAME).exists()


# ── Exact Replay ──────────────────────────────────────────

def replay(tmp_path, registry, *, script=None, repeats=1, backend=Backend.RISK_DEFENDER):
    source, _, population = execute(tmp_path, registry, backend=backend)
    paths = load_paths(source.run_dir)
    config = config_from_source(load_source_config(source.run_dir), experiment_id="src" + REPLAY_SUFFIX,
                                arms=ARMS, output_root=tmp_path)
    client = RoleClient(script or SCRIPT * len(ARMS) * repeats)
    outcomes = replay_paths(paths, config, client=client, population=population, retriever=OneCommentRetriever(),
                            repeats=repeats, source_config={"experiment_id": "src"})
    return source, config, paths, outcomes, client


def test_replay_reuses_archived_queries_without_mutation_llm(tmp_path, registry):
    _, _, paths, outcomes, client = replay(tmp_path, registry)
    assert client.count("fuzzer") == 0
    archived = [q["query"] for q in paths[0]["queries"]]
    assert client.prompts_of("target")[:3] == client.prompts_of("target")[3:]      # 두 갈래에 같은 질문
    for outcome in outcomes:
        assert [t.attack.query for t in outcome.result.turns] == archived
        assert {t.attack.reset_reason for t in outcome.result.turns} == {"REPLAY"}


def test_replay_starts_from_empty_state_and_compares_with_original(tmp_path, registry):
    _, _, paths, (c0, c1), _ = replay(tmp_path, registry)
    assert c0.result.k_initial == c1.result.k_initial == 8
    assert c0.runtime_recurred and c0.replay_first_violation_turn == paths[0]["first_violation_turn"]
    assert not c1.runtime_recurred and c1.replay_min_k >= 4
    assert c0.original_min_k == paths[0]["min_k"]


def test_replay_uses_fresh_target_responses(tmp_path, registry):
    """재실행의 Target이 아무것도 말하지 않으면 C0에서도 재발하지 않는다 (원래 응답을 다시 넣지 않는다)."""
    quiet = ["기록만으로는 알 수 없습니다."] * 6
    _, _, _, outcomes, _ = replay(tmp_path, registry, script=quiet)
    assert [o.runtime_recurred for o in outcomes] == [False, False]
    assert [o.replay_min_k for o in outcomes] == [8, 8]


def test_replay_repeats_get_separate_sessions(tmp_path, registry):
    _, config, _, outcomes, _ = replay(tmp_path, registry, repeats=2)
    assert [(o.arm.label, o.repeat) for o in outcomes] == [("C0", 1), ("C0", 2), ("C1", 1), ("C1", 2)]
    assert len({o.session_key for o in outcomes}) == 4
    ends = [item for item in read_trace(config.run_dir) if item["type"] == "session_end"]
    assert [item["runtime_recurred"] for item in ends] == [True, True, False, False]


def test_replay_log_is_marked_and_not_archived_again(tmp_path, registry):
    source, config, paths, _, _ = replay(tmp_path, registry)
    with (config.run_dir / CSV_FILE_NAME).open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert {row["replay_type"] for row in rows} == {"exact_replay"}
    assert all(row["experiment_id"].startswith(config.experiment_id + "|") for row in rows)
    assert not (config.run_dir / ARCHIVE_FILE_NAME).exists()
    assert load_paths(source.run_dir) == paths                                      # 원래 보관소는 그대로
    with (source.run_dir / CSV_FILE_NAME).open(encoding="utf-8") as file:
        assert {row["replay_type"] for row in csv.DictReader(file)} == {"initial"}


def test_replay_config_follows_the_source_run(tmp_path, registry):
    source, config, _, _, _ = replay(tmp_path, registry, backend=Backend.ONTOLOGY)
    assert config.backend is Backend.ONTOLOGY and config.risk.k_req == source.risk.k_req
    written = json.loads((config.run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert written["replay_of"] == ["src"] and written["backend"]["name"] == "ontology_d1"


def test_replay_fuzzer_stops_after_last_query():
    from adapters.fuzzer_adapter import NoApplicableSeedError
    from attack.selector import AttackStateView

    fuzzer = ReplayFuzzer([{"query": "q", "seed_id": "s", "objective": "O1", "scenario_code": "S1",
                            "target_qi": "AGE"}], risk_threshold=3)
    assert fuzzer.next_query(AttackStateView()).query == "q"
    with pytest.raises(NoApplicableSeedError):
        fuzzer.next_query(AttackStateView())


def test_replay_rejects_zero_repeats(tmp_path, registry):
    with pytest.raises(ValueError):
        replay(tmp_path, registry, repeats=0)


# ── 실행 집계 ─────────────────────────────────────────────

def test_report_counts_match_the_run(tmp_path, registry):
    config, results, _ = execute(tmp_path, registry)
    report = load_run(config.run_dir)
    c0, c1 = report.sessions
    assert (c0.arm, c0.turns, c0.k_min, c0.first_violation_turn) == ("C0", 3, results[0].k_min, 2)
    assert c0.paths == {"PASSTHROUGH": 3} and c0.calls["rewriter"] == 0
    assert c1.k_min == results[1].k_min and c1.first_violation_turn is None
    assert c1.suppressed_turns == sum(t.release.suppressed for t in results[1].turns)
    assert c1.calls["rewriter"] == sum(len(t.release.rounds) for t in results[1].turns if t.release.suppressed
                                       or t.release.generalized)
    assert not report.validated and report.unfinished_turns == 0


def test_report_renders_without_validation(tmp_path, registry):
    config, _, _ = execute(tmp_path, registry)
    text = render(load_run(config.run_dir))
    assert "## 갈래별 요약" in text and "없음 (python -m validation 실행 전)" in text
    assert not any(raw in text for raw in SCRIPT)                # 응답 원문은 집계에 넣지 않는다


def test_report_flags_interrupted_and_duplicated_runs(tmp_path, registry):
    config, _, _ = execute(tmp_path, registry)
    trace = config.run_dir / TRACE_FILE_NAME
    lines = trace.read_text(encoding="utf-8").splitlines()
    trace.write_text("\n".join(lines + lines + lines[:2]) + "\n", encoding="utf-8")   # 다시 실행 + 중간에 멈춘 세션
    report = load_run(config.run_dir)
    assert len(report.sessions) == 4 and report.unfinished_turns == 2
    text = render(report)
    assert "세션 요약 없이 끝난 턴이 2개" in text and "두 번 이상" in text


def test_report_lists_unresolved_released_observations(tmp_path, registry):
    config = ExperimentConfig(experiment_id="vague", target_ids=(TARGET_ID,), arms=(Arm(Condition.C0),), max_turns=1,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path)
    vague = json.dumps({"observations": [{"attribute": "OCCP", "raw_value": "코딩 일", "value_en": "zzz job",
                                          "inference_mode": "EXPLICIT", "confidence": 4,
                                          "evidence_spans": ["코딩 일"]}]}, ensure_ascii=False)
    client = RoleClient(["이 사용자는 코딩 일을 합니다."], extractor=lambda _: vague)
    run_experiment(config, client=client, population=make_population(), retriever=OneCommentRetriever(),
                   registry=registry)
    report = load_run(config.run_dir)
    assert report.sessions[0].unresolved_released == 1
    assert [(row["attribute"], row["raw_value"]) for row in report.sessions[0].unresolved] == [("OCCP", "코딩 일")]
    assert "코딩 일" in render(report)


def test_report_file_name():
    assert REPORT_FILE_NAME == "report.md"


# ── 리뷰에서 나온 경우들 ──────────────────────────────────

def test_replay_config_keeps_prompts_gate_and_temperatures():
    source = {"backend": {"name": "risk_defender"}, "risk": {"risk_threshold": 2, "generalize_threshold": 6},
              "gate": {"max_defense_rounds": 3, "refusal_text": "X", "adopt_instructed_value": False},
              "prompts": {"target": "target_system_v1", "extractor": "extractor_system_v1",
                          "rewriter": "rewriter_system_v1"},
              "temperature": {"target": 0.0, "extractor": 0.0, "rewriter": 0.0}}
    config = config_from_source(source, experiment_id="r", arms=ARMS, output_root="unused")
    assert config.extractor.prompt_version == "extractor_system_v1"
    assert (config.gate.max_defense_rounds, config.gate.refusal_text, config.gate.adopt_instructed_value) == (3, "X", False)
    assert (config.risk.risk_threshold, config.risk.generalize_threshold) == (2, 6)
    assert config.target.temperature == 0.0


def test_replay_run_config_records_prompts_and_gate(tmp_path, registry):
    _, config, _, _, _ = replay(tmp_path, registry)
    written = json.loads((config.run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert written["prompts"]["extractor"] == config.extractor.prompt_version
    assert written["gate"]["adopt_instructed_value"] is True


def test_duplicate_path_ids_get_separate_replay_sessions(tmp_path, registry):
    source, _, population = execute(tmp_path, registry)
    paths = load_paths(source.run_dir) * 2                      # 같은 experiment_id로 두 번 실행한 보관소
    config = config_from_source(load_source_config(source.run_dir), experiment_id="dup", arms=(Arm(Condition.C0),),
                                output_root=tmp_path)
    outcomes = replay_paths(paths, config, client=RoleClient(SCRIPT * 2), population=population,
                            retriever=OneCommentRetriever())
    assert len({o.session_key for o in outcomes}) == 2


def test_report_separates_an_interrupted_session_from_the_rerun(tmp_path, registry):
    """C0가 2턴에서 멈춘 뒤 같은 experiment_id로 다시 돌린 기록. 끊긴 2턴이 다음 세션에 합쳐지면 안 된다."""
    config, _, _ = execute(tmp_path, registry)
    trace = config.run_dir / TRACE_FILE_NAME
    lines = trace.read_text(encoding="utf-8").splitlines()
    trace.write_text("\n".join(lines[:2] + lines) + "\n", encoding="utf-8")
    report = load_run(config.run_dir)
    assert [(s.arm, s.turns) for s in report.sessions] == [("C0", 3), ("C1", 3)]
    assert report.unfinished_turns == 2


def test_vague_answer_becomes_a_refinement_target(registry):
    """표준 값으로 풀지 못한 답("코딩 일")이 공개돼 있으면 그 QI의 O2(구체화) Seed를 쓸 수 있다."""
    from attack.selector import AttackStateView, SeedSelector

    selector = SeedSelector(registry, rng_seed=1)
    plain = AttackStateView({"OCCP": "코딩 일"}, prior_turns=1, refinable_qis=frozenset())
    vague = AttackStateView({"OCCP": "코딩 일"}, prior_turns=1, refinable_qis=frozenset(),
                            unresolved_qis=frozenset({"OCCP"}))
    assert not any(s.target_qi == "OCCP" for s in selector.applicable(plain))
    occp = [s for s in selector.applicable(vague) if s.target_qi == "OCCP"]
    assert occp and all(s.objective == "O2" for s in occp)


def test_scope_falls_back_to_other_seeds_instead_of_stopping(registry):
    from attack.selector import AttackStateView, SeedSelector

    selector = SeedSelector(registry, rng_seed=1)
    exhausted = {"AGE": "34", "LOC": "Zurich, Switzerland", "OCCP": "software engineer", "EDU": "Master's",
                 "REL": "single"}
    view = AttackStateView(exhausted, prior_turns=3, refinable_qis=frozenset(),
                           target_qi_scope=frozenset(exhausted))
    assert selector.applicable(view) == ()
    seed, count = selector.choose(view)
    assert seed is not None and seed.target_qi not in exhausted and count > 0


def test_report_shows_question_source_and_rejected_mutations(tmp_path, registry):
    config = ExperimentConfig(experiment_id="paired", target_ids=(TARGET_ID,), arms=ARMS, max_turns=3,
                              risk=RiskConfig(risk_threshold=3), output_root=tmp_path, paired_questions=True)
    run_experiment(config, client=RoleClient(SCRIPT * 2), population=make_population(),
                   retriever=OneCommentRetriever(), registry=registry)
    report = load_run(config.run_dir)
    assert [s.questions_from for s in report.sessions] == [None, "C0"]
    text = render(report)
    assert "C0의 질문 그대로" in text and "| 갈래 간 같은 질문 | True |" in text
