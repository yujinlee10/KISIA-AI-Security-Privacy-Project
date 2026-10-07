# orchestrator/recompute.py · stop_on_violation 테스트 — 기록된 응답으로 다시 계산하기 (가짜 LLM)

from __future__ import annotations

import csv
import json

import pytest

from adapters.risk_adapter import RiskConfig
from attack.registry import SeedRegistry
from orchestrator.experiment import Backend, ExperimentConfig, build_risk_engine, run_experiment
from orchestrator.recompute import RecordedExtractionClient, load_dialogues, recompute
from orchestrator.release_gate import Arm, Condition, ReleasePath
from orchestrator.replay import config_from_source, load_source_config
from orchestrator.run_log import CSV_FILE_NAME, TRACE_FILE_NAME
from orchestrator.session import StopReason
from tests.orchestrator.sample_data import RAW_T1, RAW_T2, RAW_T3, OneCommentRetriever, RoleClient, make_population

ARMS = (Arm(Condition.C0), Arm(Condition.C1))
SCRIPT = [RAW_T1, RAW_T2, RAW_T3]


@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


def original(tmp_path, registry, targets=("p1",), **overrides):
    config = ExperimentConfig(experiment_id="src", target_ids=tuple(targets), arms=ARMS, backend=Backend.ONTOLOGY,
                              max_turns=3, risk=RiskConfig(risk_threshold=3), output_root=tmp_path,
                              paired_questions=True, **overrides)
    population = make_population()
    results = run_experiment(config, client=RoleClient(SCRIPT * len(targets)), population=population,
                             retriever=OneCommentRetriever(), registry=registry,
                             engine=build_risk_engine(Backend.ONTOLOGY, population, config.risk))
    return config, results, population


def again(tmp_path, source, population, *, client=None, experiment_id="again", targets=None):
    dialogues = load_dialogues(source.run_dir, targets)
    config = config_from_source(load_source_config(source.run_dir), experiment_id=experiment_id, arms=ARMS,
                                output_root=tmp_path)
    results, stats = recompute(dialogues, config, population=population, retriever=OneCommentRetriever(),
                               client=client, source_config={"experiment_id": "src"})
    return config, results, stats


def k_path(result):
    return [(t.commit.k_before, t.commit.k_after) for t in result.turns]


# ── 다시 계산 ─────────────────────────────────────────────

def test_offline_recompute_reproduces_every_arm_without_any_llm_call(tmp_path, registry):
    """원 응답 추출, 재작성, 고쳐 쓴 응답의 재추출이 모두 기록에 있다. 설정이 같으면 한 번도 부르지 않고 같은 결과가 나온다."""
    source, first, population = original(tmp_path, registry)
    _, results, stats = again(tmp_path, source, population, client=None)
    assert stats.live_calls == 0
    assert [k_path(r) for r in results] == [k_path(r) for r in first]
    assert [[t.attack.query for t in r.turns] for r in results] == [[t.attack.query for t in r.turns] for r in first]
    assert [[t.release.path for t in r.turns] for r in results] == [[t.release.path for t in r.turns] for r in first]
    assert [[t.release.released_response for t in r.turns] for r in results] == \
           [[t.release.released_response for t in r.turns] for r in first]


def test_recompute_never_calls_target_or_mutation_llm(tmp_path, registry):
    source, _, population = original(tmp_path, registry)
    client = RoleClient([])                                  # Target 대본이 비어 있다: 부르면 오류가 난다
    again(tmp_path, source, population, client=client)
    assert client.count("target") == 0 and client.count("fuzzer") == 0


def strip_reextraction_outputs(source):
    """재추출 출력을 남기기 전의 trace처럼 만든다 (run_multi10 같은 예전 실행)."""
    trace = source.run_dir / TRACE_FILE_NAME
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    for item in lines:
        for round_item in (item.get("release") or {}).get("rounds") or []:
            round_item.pop("extraction_raw_output", None)
    trace.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n", encoding="utf-8")


def test_old_trace_needs_only_reextraction_calls(tmp_path, registry):
    """예전 trace에는 재추출 출력이 없다. 재작성 결과는 다시 쓰고, 고쳐 쓴 응답의 추출만 실제로 부른다."""
    source, first, population = original(tmp_path, registry)
    strip_reextraction_outputs(source)
    client = RoleClient([])
    _, results, stats = again(tmp_path, source, population, client=client)
    assert [k_path(r) for r in results] == [k_path(r) for r in first]
    assert client.count("rewriter") == 0 and client.count("extractor") == stats.live_calls > 0


def test_offline_recompute_of_old_trace_refuses_where_reextraction_is_missing(tmp_path, registry):
    source, first, population = original(tmp_path, registry)
    strip_reextraction_outputs(source)
    _, (c0, c1), stats = again(tmp_path, source, population, client=None)
    assert stats.live_calls == 0
    assert k_path(c0) == k_path(first[0])                    # Defender가 없는 갈래는 그대로다
    rewritten = [t.turn for t in first[1].turns if t.release.path is ReleasePath.REWRITTEN]
    assert rewritten and [t.turn for t in c1.turns if t.release.path is ReleasePath.REFUSAL] == rewritten
    assert c1.k_min >= first[1].k_min                        # 통째로 막았으므로 더 보수적이다


def test_changed_threshold_changes_the_defenders_decisions(tmp_path, registry):
    """위험 기준을 낮추면(k_req 4 → 2) Defender가 덜 개입한다. 같은 대화에서 C1의 k가 더 내려간다."""
    from orchestrator.recompute import apply_overrides

    source, first, population = original(tmp_path, registry)
    dialogues = load_dialogues(source.run_dir)
    config = config_from_source(load_source_config(source.run_dir), experiment_id="src__recomputed", arms=ARMS,
                                output_root=tmp_path)
    config, suffix = apply_overrides(config, threshold=1)
    assert suffix == "_t1" and config.risk.k_req == 2
    client = RoleClient([])
    (c0, c1), _ = recompute(dialogues, config, population=population, retriever=OneCommentRetriever(), client=client)
    assert k_path(c0) == k_path(first[0])
    assert c1.k_min < first[1].k_min and c1.k_min >= 2
    assert apply_overrides(config) == (config, "")           # 바꾼 것이 없으면 꼬리표도 없다


def test_recompute_applies_the_current_rules_to_recorded_extractor_output(tmp_path, registry):
    """기록에는 추출기 출력 원문이 남아 있다. 해석 규칙이 바뀌면 다시 계산한 결과도 바뀐다."""
    source, first, population = original(tmp_path, registry)
    trace = source.run_dir / TRACE_FILE_NAME
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    for item in lines:                                       # 추출기가 '스위스' 대신 읽을 수 없는 표현을 냈던 것으로 바꾼다
        if item["type"] == "turn" and item["turn"] == 1:
            item["raw_extraction"]["raw_output"] = item["raw_extraction"]["raw_output"].replace(
                '"Switzerland"', '"somewhere quiet"').replace('"스위스"', '"스위스에"')
    trace.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n", encoding="utf-8")
    _, (c0, _), _ = again(tmp_path, source, population)
    assert c0.turns[0].commit.k_after == 8 and first[0].turns[0].commit.k_after == 5
    assert c0.unresolved_released == 1


def test_recompute_output_is_marked_and_keeps_the_source_untouched(tmp_path, registry):
    source, _, population = original(tmp_path, registry)
    before = (source.run_dir / TRACE_FILE_NAME).read_text(encoding="utf-8")
    config, _, _ = again(tmp_path, source, population)
    with (config.run_dir / CSV_FILE_NAME).open(encoding="utf-8") as file:
        assert {row["replay_type"] for row in csv.DictReader(file)} == {"recomputed"}
    written = json.loads((config.run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert written["recomputed_from"] == "src" and written["offline"] is True
    assert (source.run_dir / TRACE_FILE_NAME).read_text(encoding="utf-8") == before


def test_dialogues_skip_followers_and_sessions_cut_by_llm_error(tmp_path, registry):
    source, _, _ = original(tmp_path, registry, targets=("p1", "p5"))
    assert [(d.target_id, len(d.queries)) for d in load_dialogues(source.run_dir)] == [("p1", 3), ("p5", 3)]
    assert [d.target_id for d in load_dialogues(source.run_dir, ["p5"])] == ["p5"]
    trace = source.run_dir / TRACE_FILE_NAME
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    for item in lines:
        if item["type"] == "session_end" and item["target_id"] == "p5" and item["arm"] == "C0":
            item["stop_reason"] = "LLM_ERROR"
    trace.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n", encoding="utf-8")
    assert [d.target_id for d in load_dialogues(source.run_dir)] == ["p1"]


def test_recorded_client_only_answers_raw_extraction():
    from orchestrator.recompute import RecordedDialogue
    from qi_extractor.extractor import build_user_prompt

    turn = {"turn": 1, "attack": {"query": "q", "seed_id": "s", "objective": "O1", "scenario_code": "S1",
                                  "target_qi": "AGE"},
            "target": {"raw_candidate_response": "원 응답입니다."}, "raw_extraction": {"raw_output": '{"observations": []}'}}
    client = RecordedExtractionClient(None, [RecordedDialogue("p1", [turn])])
    assert client.complete(build_user_prompt("q", "원 응답입니다.")).text == '{"observations": []}'
    assert client.complete(build_user_prompt("q", "고쳐 쓴 응답입니다.")).text == ""      # 기록에 없는 응답 (offline)
    assert client.complete("[원래 응답]\n원 응답입니다.\n\n[수정 지시]\n1. 삭제").text == ""  # 재작성 요청 (offline)
    assert (client.recorded_hits, client.live_calls) == (1, 0)


# ── 위반에 도달하면 끝내기 ────────────────────────────────

def test_stop_on_violation_ends_the_lead_and_followers_get_the_same_questions(tmp_path, registry):
    """C0는 2턴에 k가 2가 된다. 거기서 끝내고, C1도 그 두 질문만 받는다 (C1은 스스로 끝내지 않는다)."""
    _, (c0, c1), _ = original(tmp_path, registry, stop_on_violation=True)
    assert (len(c0.turns), c0.stop_reason, c0.stop_detail) == (2, StopReason.VIOLATION_REACHED, "k=2")
    assert len(c1.turns) == 2 and c1.stop_reason is StopReason.MAX_TURNS
    assert [t.attack.query for t in c1.turns] == [t.attack.query for t in c0.turns]


def test_without_the_option_the_session_runs_all_turns(tmp_path, registry):
    _, (c0, _), _ = original(tmp_path, registry)
    assert len(c0.turns) == 3 and c0.stop_reason is StopReason.MAX_TURNS


def test_zero_match_does_not_stop_the_session(tmp_path, registry):
    """k가 0이 된 것은 위반이 아니다. 틀린 값 때문일 수 있으므로 계속 묻는다."""
    from tests.orchestrator import sample_data

    config = ExperimentConfig(experiment_id="zero", target_ids=("p1",), arms=(Arm(Condition.C0),), max_turns=3,
                              backend=Backend.ONTOLOGY, risk=RiskConfig(risk_threshold=3), output_root=tmp_path,
                              stop_on_violation=True)
    population = make_population()
    sample_data.PHRASE_OBSERVATIONS.append(("50대", {"attribute": "AGE", "raw_value": "50대", "value_en": "50s"}))
    try:
        (result,) = run_experiment(
            config, client=RoleClient([RAW_T1, "이 사용자는 여성이고 50대로 보입니다.", "더 알 수 있는 것은 없습니다."]),
            population=population, retriever=OneCommentRetriever(), registry=registry,
            engine=build_risk_engine(Backend.ONTOLOGY, population, config.risk))
    finally:
        del sample_data.PHRASE_OBSERVATIONS[-1:]
    assert [t.commit.k_after for t in result.turns] == [5, 0, 0] and result.stop_reason is StopReason.MAX_TURNS
