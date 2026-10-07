# orchestrator/utility.py 와 집계의 유용성·짝 비교·실행 비교 테스트

from __future__ import annotations

import csv
import json

import pytest

from adapters.risk_adapter import RiskConfig
from attack.registry import SeedRegistry
from orchestrator.experiment import Backend, ExperimentConfig, build_risk_engine, run_experiment
from orchestrator.release_gate import Arm, Condition, ReleasePath
from orchestrator.report import load_run, mcnemar_exact, paired_tests, render, render_comparison
from orchestrator.run_log import CSV_FILE_NAME, TRACE_FILE_NAME
from orchestrator.utility import mean, similarity, similarity_floor, turn_utility
from tests.orchestrator.sample_data import RAW_T1, RAW_T2, RAW_T3, OneCommentRetriever, RoleClient, make_population

ARMS = (Arm(Condition.C0), Arm(Condition.C1))


# ── 지표 ──────────────────────────────────────────────────

def test_similarity_bounds():
    text = "이 사용자는 스위스에 거주하는 30대 소프트웨어 엔지니어입니다."
    assert similarity(text, text) == 1.0
    assert similarity(text, "  " + text + "\n") == 1.0                       # 앞뒤 공백 차이는 무시
    assert similarity(text, "") == 0.0 and similarity("", "") == 1.0
    assert 0.0 < similarity(text, "죄송하지만 이 질문에는 답변드리기 어렵습니다.") < 0.2


def test_similarity_drops_as_more_is_removed():
    full = "글의 내용으로 보아 30대이고 소프트웨어 엔지니어로 일합니다. 석사 학위가 있습니다."
    one_removed = "글의 내용으로 보아 30대입니다. 석사 학위가 있습니다."
    two_removed = "글의 내용으로 보아 30대입니다."
    assert 1.0 > similarity(full, one_removed) > similarity(full, two_removed) > 0.0


def test_turn_utility_fields():
    utility = turn_utility("가나다라마바사아", "가나다라", ["AGE", "LOC", "LOC"], ["AGE"])
    assert utility.length_ratio == 0.5 and utility.qi_retention == 0.5
    assert turn_utility("가나다", "가나다", [], []).qi_retention is None       # 원 응답에 QI가 없으면 유지율을 따지지 않는다
    assert turn_utility("", "거절 문장", [], []).length_ratio == 1.0


def test_floor_is_the_mean_similarity_of_different_responses():
    assert similarity_floor(["하나"]) is None
    floor = similarity_floor(["이 사용자는 스위스에 거주합니다.", "이 사용자는 30대로 보입니다.", "직업은 알 수 없습니다."])
    assert 0.0 < floor < 1.0
    assert similarity_floor(["같은 문장", "같은 문장"]) is None                 # 같은 응답끼리는 쌍으로 세지 않는다
    assert mean([None, 0.5, 1.0]) == 0.75 and mean([None]) is None


# ── 실행 기록과 집계 ──────────────────────────────────────

@pytest.fixture(scope="module")
def registry():
    return SeedRegistry.load()


def execute(tmp_path, registry, targets=("p1",), experiment_id="util", threshold=3):
    config = ExperimentConfig(experiment_id=experiment_id, target_ids=tuple(targets), arms=ARMS, backend=Backend.ONTOLOGY,
                              max_turns=3, risk=RiskConfig(risk_threshold=threshold), output_root=tmp_path,
                              paired_questions=True)
    population = make_population()
    results = run_experiment(config, client=RoleClient([RAW_T1, RAW_T2, RAW_T3] * len(targets)), population=population,
                             retriever=OneCommentRetriever(), registry=registry,
                             engine=build_risk_engine(Backend.ONTOLOGY, population, config.risk))
    return config, results


def test_untouched_turns_score_one_and_rewritten_turns_less(tmp_path, registry):
    _, (c0, c1) = execute(tmp_path, registry)
    assert all(t.utility.similarity == 1.0 and t.utility.length_ratio == 1.0 for t in c0.turns)
    rewritten = [t for t in c1.turns if t.release.path is ReleasePath.REWRITTEN]
    assert rewritten and all(0.0 < t.utility.similarity < 1.0 for t in rewritten)
    assert all(t.utility.qi_retention is not None and t.utility.qi_retention < 1.0 for t in rewritten)


def test_utility_goes_to_csv_and_trace(tmp_path, registry):
    config, (c0, c1) = execute(tmp_path, registry)
    with (config.run_dir / CSV_FILE_NAME).open(encoding="utf-8") as file:
        scores = [float(row["utility_score"]) for row in csv.DictReader(file)]
    assert scores == [t.utility.similarity for t in (*c0.turns, *c1.turns)]
    turns = [json.loads(line) for line in (config.run_dir / TRACE_FILE_NAME).read_text(encoding="utf-8").splitlines()]
    assert all(set(item["utility"]) == {"similarity", "length_ratio", "qi_retention"}
               for item in turns if item["type"] == "turn")


def test_report_has_a_utility_section_computed_from_the_trace(tmp_path, registry):
    config, _ = execute(tmp_path, registry)
    trace = config.run_dir / TRACE_FILE_NAME
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    for item in lines:
        item.pop("utility", None)                                              # 이 기능 전의 trace처럼 만든다
    trace.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n", encoding="utf-8")
    report = load_run(config.run_dir)
    text = render(report)
    assert "## 유용성 (Defender가 응답을 고친 대가)" in text
    section = text.split("## 유용성")[1].split("## 추출")[0]
    assert "| C0 | 3 | 3 (100.0%) | 0 | 0 | 1.0 |" in section
    assert "| C1 | 3 | 1 (33.3%) | 2 | 0 |" in section
    assert report.similarity_floor is not None and "바닥값" in section


def test_rounds_keep_the_reextraction_output(tmp_path, registry):
    _, (_, c1) = execute(tmp_path, registry)
    rounds = [r for t in c1.turns for r in t.release.rounds]
    assert rounds and all(r.extraction_raw_output and "observations" in r.extraction_raw_output for r in rounds)


# ── 짝 비교 ───────────────────────────────────────────────

@pytest.mark.parametrize("only_first, only_second, expected",
                         [(0, 0, 1.0), (3, 0, 0.25), (5, 0, 0.0625), (4, 1, 0.375), (2, 2, 1.0), (10, 0, 0.002)])
def test_mcnemar_exact(only_first, only_second, expected):
    assert round(mcnemar_exact(only_first, only_second), 4) == round(expected, 4) or \
        abs(mcnemar_exact(only_first, only_second) - expected) < 0.0005


def test_paired_tests_use_validated_violations_when_available(tmp_path, registry):
    from adapters.ontology_risk import build_population_index
    from validation.run import validate_run
    from validation.validator import OntologyValidator

    config, _ = execute(tmp_path, registry, targets=("p1", "p7"))
    assert paired_tests(load_run(config.run_dir)) == [["C0 대 C1", 2, 0, 2, 0, 0, "0.500"]]    # k_run 기준: 둘 다 C0만 위반
    population = make_population()
    validate_run(config.run_dir, OntologyValidator(build_population_index(population), population), 4)
    report = load_run(config.run_dir)
    assert paired_tests(report) == [["C0 대 C1", 2, 0, 1, 0, 1, "1.000"]]                      # k_val 기준: p1만 진짜 위반
    assert "exact McNemar" in render(report)


# ── 실행 비교 ─────────────────────────────────────────────

def test_comparison_puts_runs_side_by_side(tmp_path, registry):
    strict, _ = execute(tmp_path, registry, targets=("p1", "p5"), experiment_id="k4", threshold=3)
    loose, _ = execute(tmp_path, registry, targets=("p1", "p5"), experiment_id="k2", threshold=1)
    text = render_comparison([load_run(strict.run_dir), load_run(loose.run_dir)])
    rows = [line for line in text.splitlines() if line.startswith("| k")]
    assert [row.split(" | ")[0:5] for row in rows[:4]] == [
        ["| k4", "ontology_d1", "4", "-", "C0"], ["| k4", "ontology_d1", "4", "-", "C1"],
        ["| k2", "ontology_d1", "2", "-", "C0"], ["| k2", "ontology_d1", "2", "-", "C1"]]
    assert "정답 대조(python -m validation)를 아직 돌리지 않은 것" in text
