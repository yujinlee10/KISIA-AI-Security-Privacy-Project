# validation 테스트 — QI 판정, k_val, 실행 폴더 검증 결과 파일

from __future__ import annotations

import csv
import json

import pytest

from adapters.ontology_risk import build_population_index
from adapters.risk_adapter import RiskConfig
from attack.registry import SeedRegistry
from orchestrator.experiment import Backend, ExperimentConfig, build_risk_engine, run_experiment
from orchestrator.release_gate import Arm, Condition
from tests.orchestrator.sample_data import (
    RAW_T1,
    RAW_T2,
    RAW_T3,
    TARGET_ID,
    OneCommentRetriever,
    RoleClient,
    make_population,
)
from validation.run import (
    NOT_VALIDATED,
    VALIDATED_ARCHIVE_FILE_NAME,
    VALIDATED_CSV_FILE_NAME,
    VALIDATED_HIGH_RISK,
    VALIDATION_FILE_NAME,
    validate_run,
)
from validation.validator import OntologyValidator, RiskDefenderValidator, UnknownTargetError, Verdict

K_REQ = 4


@pytest.fixture(scope="module")
def population():
    return make_population()


@pytest.fixture(scope="module")
def legacy(population):
    return RiskDefenderValidator(population)


@pytest.fixture(scope="module")
def onto(population):
    return OntologyValidator(build_population_index(population), population)


# ── risk_defender 형식 ────────────────────────────────────

def test_legacy_correct_values_keep_every_constraint(legacy):
    result = legacy.validate("p1", {"AGE": "30s", "LOC": "Zurich, Switzerland"})
    assert result.verdicts == {"AGE": Verdict.CORRECT, "LOC": Verdict.CORRECT}
    assert result.k_val == 3 and result.target_in_run_candidates


def test_legacy_generalized_value_is_less_precise(legacy):
    result = legacy.validate("p1", {"LOC": "Switzerland"})
    assert result.verdicts == {"LOC": Verdict.LESS_PRECISE}
    assert result.k_val == 5


def test_legacy_wrong_value_is_dropped_from_k_val(legacy):
    """틀린 직업(간호사)을 빼면 30대·취리히 3명. 실행 중 k_run은 p2 한 명이었고 그 안에 대상(p1)은 없다."""
    result = legacy.validate("p1", {"AGE": "30s", "LOC": "Zurich, Switzerland", "OCCP": "nurse"})
    assert result.verdicts["OCCP"] is Verdict.INCORRECT
    assert result.validated_state == {"AGE": "30s", "LOC": "Zurich, Switzerland"}
    assert result.k_val == 3 and not result.target_in_run_candidates


def test_legacy_qi_missing_from_truth_is_unassessable(legacy):
    result = legacy.validate("p1", {"INC": "high"})
    assert result.verdicts == {"INC": Verdict.UNASSESSABLE}
    assert result.k_val == 8


def test_empty_state_counts_everyone(legacy, onto):
    assert legacy.validate("p1", {}).k_val == 8
    assert onto.validate("p1", {}).k_val == 8


def test_unknown_target_is_rejected(legacy, onto):
    with pytest.raises(UnknownTargetError):
        legacy.validate("nobody", {})
    with pytest.raises(UnknownTargetError):
        onto.validate("nobody", {"SEX": "SEX:MALE"})


# ── 온톨로지 형식 ─────────────────────────────────────────

def node(onto_validator, attribute, value):
    return onto_validator._mapper.node_for(attribute, value)


def test_ontology_exact_and_ancestor_nodes(onto):
    age_exact = node(onto, "AGE", 34)
    decade = onto._ontology.parent(onto._ontology.parent(age_exact))           # 정확한 나이 → 5년 구간 → 10년 구간
    result = onto.validate("p1", {"AGE": age_exact, "LOC": "LOC:C:CH"})
    assert result.verdicts == {"AGE": Verdict.CORRECT, "LOC": Verdict.LESS_PRECISE}
    assert result.k_val == 1
    assert onto.validate("p1", {"AGE": decade}).verdicts == {"AGE": Verdict.LESS_PRECISE}


def test_ontology_wrong_branch_is_incorrect(onto):
    """대상은 high인데 State에 middle이 들어간 경우 (실제 실행에서 나온 유형)."""
    state = {"LOC": "LOC:C:CH", "INC": node(onto, "INC", "middle")}
    result = onto.validate("p1", state)
    assert result.verdicts["INC"] is Verdict.INCORRECT
    assert result.validated_state == {"LOC": "LOC:C:CH"}
    assert result.k_val == 5 and result.k_val >= onto._index.count(state)


def test_ontology_unknown_node_is_unassessable(onto):
    assert onto.validate("p1", {"OCCP": "OCCP:no-such-node"}).verdicts == {"OCCP": Verdict.UNASSESSABLE}


def test_k_val_never_below_one_or_k_run(onto):
    """대상 본인은 검증된 조건을 항상 만족하므로 k_val >= 1 이고, 조건이 줄 뿐이라 k_val >= k_run 이다."""
    states = [{"SEX": "SEX:FEMALE"}, {"SEX": "SEX:MALE", "REL": node(onto, "REL", "married")},
              {"AGE": node(onto, "AGE", 52), "LOC": "LOC:C:DE"}]
    for target_id in ("p1", "p4", "p7"):
        for state in states:
            result = onto.validate(target_id, state)
            assert result.k_val >= max(1, onto._index.count(state))


# ── 실행 폴더 검증 ────────────────────────────────────────

@pytest.fixture
def run_dir(tmp_path, population):
    config = ExperimentConfig(experiment_id="exp_val", target_ids=(TARGET_ID,),
                              arms=(Arm(Condition.C0), Arm(Condition.C1)), backend=Backend.ONTOLOGY,
                              max_turns=3, risk=RiskConfig(risk_threshold=3), output_root=tmp_path)
    run_experiment(config, client=RoleClient([RAW_T1, RAW_T2, RAW_T3] * 2), population=population,
                   retriever=OneCommentRetriever(), registry=SeedRegistry.load(),
                   engine=build_risk_engine(Backend.ONTOLOGY, population, config.risk))
    return config.run_dir


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_validate_run_reports_each_session(run_dir, onto):
    """대본의 응답은 모두 대상의 실제 값이라 k_val = k_run 이다. C0는 턴 2에 기준 아래로 내려가고 C1은 막는다."""
    c0, c1 = validate_run(run_dir, onto, K_REQ)
    assert (c0.arm, c0.k_run_min, c0.k_val_min) == ("C0", 1, 1)
    assert (c0.runtime_first_violation_turn, c0.validated_first_violation_turn) == (2, 2)
    assert c0.validated_high_risk and c0.incorrect_qis == ()
    assert (c1.arm, c1.k_val_min, c1.validated_first_violation_turn) == ("C1", 4, None)
    assert c0.archived_path_id and c1.archived_path_id is None


def test_validate_run_writes_turn_and_session_lines(run_dir, onto):
    validate_run(run_dir, onto, K_REQ)
    lines = read_jsonl(run_dir / VALIDATION_FILE_NAME)
    assert [line["type"] for line in lines] == ["turn"] * 3 + ["session"] + ["turn"] * 3 + ["session"]
    assert [line["k_val"] for line in lines if line["type"] == "turn"] == [5, 2, 1, 5, 4, 4]
    assert all(line["target_in_run_candidates"] for line in lines if line["type"] == "turn")


def test_validated_csv_fills_k_val_and_keeps_the_original(run_dir, onto):
    validate_run(run_dir, onto, K_REQ)
    with (run_dir / "experiment_log.csv").open(encoding="utf-8") as file:
        original = list(csv.DictReader(file))
    with (run_dir / VALIDATED_CSV_FILE_NAME).open(encoding="utf-8") as file:
        validated = list(csv.DictReader(file))
    assert [row["k_val"] for row in original] == [""] * 6
    assert [row["k_val"] for row in validated] == ["5", "2", "1", "5", "4", "4"]
    assert [row["k_run_after"] for row in validated] == [row["k_run_after"] for row in original]


def test_archived_path_gets_validated_status(run_dir, onto):
    validate_run(run_dir, onto, K_REQ)
    (record,) = read_jsonl(run_dir / VALIDATED_ARCHIVE_FILE_NAME)
    assert record["validation_status"] == VALIDATED_HIGH_RISK
    assert (record["k_val_min"], record["validated_first_violation_turn"]) == (1, 2)
    (source,) = read_jsonl(run_dir / "high_risk_paths.jsonl")
    assert source["validation_status"] == "UNVALIDATED"            # 원본 보관소는 그대로 둔다


def test_runtime_violation_built_on_wrong_values_is_not_validated(run_dir, population):
    """
    같은 실행을 다른 사람(p7: 독일·50대·간호사)의 것으로 보고 검증한다.
    State의 값이 전부 그 사람과 맞지 않으므로 k_val은 줄지 않고, 실행 중 위반은 검증되지 않는다.
    """
    trace = run_dir / "trace.jsonl"
    trace.write_text(trace.read_text(encoding="utf-8").replace('"target_id": "p1"', '"target_id": "p7"'),
                     encoding="utf-8")
    validator = OntologyValidator(build_population_index(population), population)
    c0, _ = validate_run(run_dir, validator, K_REQ)
    assert c0.runtime_first_violation_turn == 2 and c0.validated_first_violation_turn is None
    assert c0.k_val_min == 8 and set(c0.incorrect_qis) >= {"LOC", "AGE", "OCCP"}
    (record,) = read_jsonl(run_dir / VALIDATED_ARCHIVE_FILE_NAME)
    assert record["validation_status"] == NOT_VALIDATED


def test_validation_can_be_repeated(run_dir, onto):
    first = validate_run(run_dir, onto, K_REQ)
    assert validate_run(run_dir, onto, K_REQ) == first
    assert len(read_jsonl(run_dir / VALIDATION_FILE_NAME)) == 8


def test_missing_trace_is_reported(tmp_path, onto):
    with pytest.raises(FileNotFoundError):
        validate_run(tmp_path, onto, K_REQ)


def test_report_picks_up_k_val_after_validation(run_dir, onto):
    from orchestrator.report import load_run, render

    assert not load_run(run_dir).validated
    validate_run(run_dir, onto, K_REQ)
    report = load_run(run_dir)
    assert report.validated
    assert [(s.k_val_min, s.validated_first_violation_turn) for s in report.sessions] == [(1, 2), (4, None)]
    assert "| 정답 대조(k_val) | 있음 |" in render(report)


def test_rerun_with_same_experiment_id_validates_each_archived_path_separately(run_dir, population):
    """같은 path_id가 두 번 보관된 경우: 첫 실행은 맞는 값으로, 둘째 실행은 다른 사람(p7)으로 본다."""
    trace, archive = run_dir / "trace.jsonl", run_dir / "high_risk_paths.jsonl"
    first = trace.read_text(encoding="utf-8")
    trace.write_text(first + first.replace('"target_id": "p1"', '"target_id": "p7"'), encoding="utf-8")
    archive.write_text(archive.read_text(encoding="utf-8") * 2, encoding="utf-8")
    csv_path = run_dir / "experiment_log.csv"
    header, *rows = csv_path.read_text(encoding="utf-8").splitlines()
    csv_path.write_text("\n".join([header] + rows + rows) + "\n", encoding="utf-8")

    validator = OntologyValidator(build_population_index(population), population)
    assert len(validate_run(run_dir, validator, K_REQ)) == 4
    statuses = [record["validation_status"] for record in read_jsonl(run_dir / VALIDATED_ARCHIVE_FILE_NAME)]
    assert statuses == [VALIDATED_HIGH_RISK, NOT_VALIDATED]


def test_interrupted_session_is_validated_on_its_own(run_dir, onto):
    trace = run_dir / "trace.jsonl"
    lines = trace.read_text(encoding="utf-8").splitlines()
    trace.write_text("\n".join(lines[:2] + lines) + "\n", encoding="utf-8")
    csv_path = run_dir / "experiment_log.csv"
    header, *rows = csv_path.read_text(encoding="utf-8").splitlines()
    csv_path.write_text("\n".join([header] + rows[:2] + rows) + "\n", encoding="utf-8")
    sessions = validate_run(run_dir, onto, K_REQ)
    assert [(s.arm, s.turns, s.finished) for s in sessions] == [("C0", 2, False), ("C0", 3, True), ("C1", 3, True)]


def test_csv_mismatch_writes_nothing(run_dir, onto):
    csv_path = run_dir / "experiment_log.csv"
    csv_path.write_text(csv_path.read_text(encoding="utf-8").rsplit("\n", 2)[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_run(run_dir, onto, K_REQ)
    assert not (run_dir / VALIDATION_FILE_NAME).exists()


def test_unassessable_qi_means_target_not_confirmed_in_run_candidates(legacy):
    assert not legacy.validate("p1", {"INC": "high"}).target_in_run_candidates
