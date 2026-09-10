from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlretrieve

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from risk_defender.defender import defend_candidate_qis
    from risk_defender.loader import load_population
    from risk_defender.risk import calculate_k, evaluate_projected_risk
    from risk_defender.schema import PrivacyState, QIObservation
else:
    from .defender import defend_candidate_qis
    from .loader import load_population
    from .risk import calculate_k, evaluate_projected_risk
    from .schema import PrivacyState, QIObservation


DEFAULT_DATA_URL = (
    "https://raw.githubusercontent.com/eth-sri/SynthPAI/main/"
    "data/profiles/user_bot_profiles_300.json"
)
DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "data" / "user_bot_profiles_300.json"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "outputs" / "risk_defender_demo.csv"

DEMO_QI_ORDER = [
    "location",
    "occupation",
    "age",
    "education",
    "relationship_status",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the SynthPAI Privacy Risk / Defender demo."
    )
    parser.add_argument(
        "--data",
        default=None,
        help=(
            "Path to SynthPAI data/synthpai.jsonl or "
            "data/profiles/user_bot_profiles_300.json. "
            "If omitted, the profile dataset is downloaded from GitHub."
        ),
    )
    parser.add_argument("--data-url", default=DEFAULT_DATA_URL)
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument("--target-id", default=None)
    parser.add_argument("--threshold", type=int, default=5)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_PATH))
    args = parser.parse_args()

    data_path = Path(args.data) if args.data else ensure_default_dataset(
        args.data_url,
        refresh=args.refresh_data,
    )
    population = load_population(data_path)
    if not population:
        raise ValueError("No population records loaded.")

    target = next(
        (record for record in population if record.person_id == args.target_id),
        population[0],
    )
    state = PrivacyState(target_id=target.person_id, session_id="demo-session")

    rows = []
    for turn, qi_type in enumerate(DEMO_QI_ORDER, start=1):
        candidate = QIObservation(
            qi_type=qi_type,
            value=target.qis.get(qi_type),
            source_turn=turn,
            confidence=1.0,
            source="ground_truth_demo",
        )
        projected = evaluate_projected_risk(
            population,
            state,
            [candidate],
            threshold=args.threshold,
        )
        decision = defend_candidate_qis(
            population,
            state,
            [candidate],
            threshold=args.threshold,
        )
        state = decision.final_state
        rows.append(
            {
                "turn": turn,
                "target_id": target.person_id,
                "candidate_qi": f"{candidate.qi_type}={candidate.value}",
                "k_before": projected.k_before,
                "k_after_raw": projected.k_after,
                "risk_gain_raw": round(projected.risk_gain, 6),
                "risky": projected.risky,
                "defense_action": decision.action,
                "generalized_qis": decision.generalized_qis,
                "suppressed_qis": [
                    f"{item.qi_type}={item.value}" for item in decision.suppressed_qis
                ],
                "k_after_defense": decision.final_k,
                "risk_after_defense": round(decision.final_risk, 6),
                "state_after_defense": state.committed_qis(),
            }
        )

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Loaded population: {len(population)}")
    print(f"Dataset: {data_path}")
    print(f"Target: {target.person_id}")
    print(f"Final state: {state.committed_qis()}")
    print(f"Final k: {calculate_k(population, state).k}")
    print(f"Saved demo CSV: {output_path}")


def ensure_default_dataset(data_url: str, *, refresh: bool = False) -> Path:
    if DEFAULT_DATA_PATH.exists() and not refresh and _is_valid_json(DEFAULT_DATA_PATH):
        return DEFAULT_DATA_PATH

    DEFAULT_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DEFAULT_DATA_PATH.with_suffix(".download")
    try:
        urlretrieve(data_url, temp_path)
    except URLError as exc:
        try:
            subprocess.run(
                ["curl", "-L", "--fail", data_url, "-o", str(temp_path)],
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as curl_exc:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Failed to download SynthPAI profile dataset. "
                "Check your network connection or pass --data with a local file path."
            ) from curl_exc

    if not _is_valid_json(temp_path):
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Downloaded SynthPAI profile dataset is not valid JSON. "
            "Try --refresh-data again or pass --data with a local file path."
        )

    temp_path.replace(DEFAULT_DATA_PATH)
    return DEFAULT_DATA_PATH


def _is_valid_json(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as file:
            json.load(file)
    except (OSError, json.JSONDecodeError):
        return False
    return True


if __name__ == "__main__":
    main()
