"""합성 환자 프로필 생성 스크립트.

원본 576개 프로필의 QI 분포를 유지하면서 합성 데이터를 생성해
총 N개(기본 1000개) 프로필을 만든다.

- AGE, SEX, REGION, DX, PROC, MED: 원본 빈도 비율로 가중치 샘플링
- PERIOD: 원본 날짜 범위(1923-02-26 ~ 2026-08-13) 내 랜덤 생성

사용법:
    python3 -m risk_defender.scripts.generate_synth_population
    python3 -m risk_defender.scripts.generate_synth_population --n 1000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import re
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SOURCE_PATH = _DATA_DIR / "user_patient_profiles_576.json"

# PERIOD 접두사
_PERIOD_PREFIX = "PERIOD_CONDITION_START_DATE_DAY_"

# 샘플링 대상 QI (PERIOD 제외)
_SAMPLE_FIELDS = ["AGE", "SEX", "REGION", "DX", "PROC", "MED"]


def _extract_weights(records: list[dict], field: str) -> tuple[list[str], list[float]]:
    """레코드 목록에서 특정 QI 필드의 값과 가중치를 추출한다."""
    vals = [r["qis"][field] for r in records if field in r["qis"]]
    counter = Counter(vals)
    total = sum(counter.values())
    values = list(counter.keys())
    weights = [counter[v] / total for v in values]
    return values, weights


def _extract_period_range(records: list[dict]) -> tuple[date, date]:
    """원본 PERIOD 값에서 날짜 범위를 추출한다."""
    dates: list[date] = []
    for r in records:
        period = r["qis"].get("PERIOD", "")
        m = re.search(r"(\d{4}-\d{2}-\d{2})$", period)
        if m:
            dates.append(date.fromisoformat(m.group(1)))
    dates.sort()
    return dates[0], dates[-1]


def _random_period(start: date, end: date, rng: random.Random) -> str:
    """날짜 범위 내 랜덤 날짜를 PERIOD 형식 문자열로 반환한다."""
    delta = (end - start).days
    rand_date = start + timedelta(days=rng.randint(0, delta))
    return f"{_PERIOD_PREFIX}{rand_date.isoformat()}"


def _generate_synth_record(
    field_weights: dict[str, tuple[list[str], list[float]]],
    period_range: tuple[date, date],
    rng: random.Random,
) -> dict:
    """합성 레코드 1개를 생성한다."""
    qis: dict[str, str] = {}
    for field, (values, weights) in field_weights.items():
        qis[field] = rng.choices(values, weights=weights, k=1)[0]
    qis["PERIOD"] = _random_period(*period_range, rng)
    return {
        "person_id": str(uuid.UUID(int=rng.getrandbits(128))),
        "qis": qis,
    }


def generate(
    source_path: Path = SOURCE_PATH,
    n: int = 1000,
    seed: int = 42,
    output_path: Path | None = None,
) -> Path:
    """원본 + 합성 데이터를 합쳐 총 n개 프로필을 생성하고 저장한다."""
    with source_path.open("r", encoding="utf-8") as f:
        originals: list[dict] = json.load(f)

    if n < len(originals):
        raise ValueError(f"n({n})은 원본 수({len(originals)})보다 커야 합니다.")

    n_synth = n - len(originals)
    print(f"원본: {len(originals)}개 / 합성 생성: {n_synth}개 / 합계: {n}개")

    # 분포 추출
    field_weights = {
        field: _extract_weights(originals, field)
        for field in _SAMPLE_FIELDS
    }
    period_range = _extract_period_range(originals)
    print(f"PERIOD 범위: {period_range[0]} ~ {period_range[1]}")

    for field, (values, weights) in field_weights.items():
        top = sorted(zip(values, weights), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{v}({w:.1%})" for v, w in top)
        print(f"  {field}: {len(values)}개 고유값, 상위3: {top_str}")

    # 합성 생성
    rng = random.Random(seed)
    synth_records = [
        _generate_synth_record(field_weights, period_range, rng)
        for _ in range(n_synth)
    ]

    combined = originals + synth_records

    if output_path is None:
        output_path = source_path.parent / f"user_patient_profiles_{n}.json"

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    print(f"저장 완료: {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="합성 환자 프로필 생성")
    parser.add_argument("--n", type=int, default=1000, help="총 프로필 수 (기본: 1000)")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드 (기본: 42)")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH, help="원본 JSON 경로")
    parser.add_argument("--output", type=Path, default=None, help="출력 JSON 경로")
    args = parser.parse_args()

    generate(
        source_path=args.source,
        n=args.n,
        seed=args.seed,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
