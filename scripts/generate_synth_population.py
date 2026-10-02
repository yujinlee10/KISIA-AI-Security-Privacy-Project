"""합성 인구 데이터 생성 스크립트.

원본 300개 프로필의 각 필드 분포를 보존하면서 가중치 샘플링으로
합성 프로필을 생성한다. 기본값은 300,000개 (원본 포함).

사용법:
    python3 scripts/generate_synth_population.py
    python3 scripts/generate_synth_population.py --n 300000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "risk_defender" / "data"
SOURCE_PATH = DATA_DIR / "user_bot_profiles_300.json"
OUTPUT_PATH = DATA_DIR / "user_bot_profiles_300000.json"

# 합성 대상 필드 (income 수치는 제외)
SYNTH_FIELDS = [
    "age",
    "sex",
    "city_country",
    "birth_city_country",
    "education",
    "occupation",
    "income_level",
    "relationship_status",
]


def load_source(path: Path) -> dict[str, dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_vocab(profiles: dict[str, dict]) -> dict[str, tuple[list, list]]:
    """각 필드별 (값 목록, 가중치 목록) 반환."""
    vocab: dict[str, tuple[list, list]] = {}
    for field in SYNTH_FIELDS:
        counter: Counter = Counter()
        for profile in profiles.values():
            val = profile.get(field)
            if val is not None:
                counter[val] += 1
        if counter:
            items = list(counter.keys())
            weights = [counter[v] for v in items]
            vocab[field] = (items, weights)
    return vocab


def generate_profiles(
    vocab: dict[str, tuple[list, list]],
    start_id: int,
    count: int,
    rng: random.Random,
) -> dict[str, dict]:
    """count개의 합성 프로필 생성. ID는 start_id부터 시작."""
    result: dict[str, dict] = {}
    for i in range(count):
        person_id = f"pers{start_id + i}"
        profile: dict = {}
        for field, (items, weights) in vocab.items():
            profile[field] = rng.choices(items, weights=weights, k=1)[0]
        result[person_id] = profile
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="합성 인구 데이터 생성")
    parser.add_argument("--n", type=int, default=300_000, help="최종 총 프로필 수 (원본 포함, 기본: 300000)")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드 (재현 가능성)")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="출력 파일 경로")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH, help="원본 파일 경로")
    args = parser.parse_args()

    source_path: Path = args.source
    output_path: Path = args.output
    total_n: int = args.n
    seed: int = args.seed

    if not source_path.exists():
        print(f"오류: 원본 파일을 찾을 수 없습니다: {source_path}", file=sys.stderr)
        sys.exit(1)

    print(f"원본 로딩: {source_path}")
    source_profiles = load_source(source_path)
    origin_count = len(source_profiles)
    synth_count = total_n - origin_count

    if synth_count <= 0:
        print(f"오류: --n({total_n})이 원본 수({origin_count})보다 커야 합니다.", file=sys.stderr)
        sys.exit(1)

    print(f"원본 프로필 수: {origin_count}")
    print(f"합성 생성 수: {synth_count}")
    print(f"랜덤 시드: {seed}")

    vocab = build_vocab(source_profiles)
    rng = random.Random(seed)

    print("합성 프로필 생성 중...")
    synth_profiles = generate_profiles(vocab, start_id=origin_count + 1, count=synth_count, rng=rng)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"저장 중: {output_path}")
    # 합성 데이터만 저장 (원본은 별도 파일 유지)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(synth_profiles, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"완료: {len(synth_profiles):,}개 프로필 저장 ({size_mb:.1f} MB)")
    print(f"로딩 시 load_expanded_population(base_path, synth_path='{output_path}') 사용")


if __name__ == "__main__":
    main()
