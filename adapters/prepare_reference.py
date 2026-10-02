# 기준 집단 데이터 준비: python -m adapters.prepare_reference
#
#   1) 원본 300명 profile을 받는다  → risk_defender.demo.ensure_default_dataset 그대로 사용
#   2) 합성 30만 명 파일이 없으면 만든다 → scripts/generate_synth_population.py 그대로 실행 (seed 42 고정)
#
# 이유진 파트의 다운로드·생성 코드를 다시 만들지 않고 호출만 한다.
# risk_defender.demo를 직접 실행하면 outputs/demo_single_target.csv(커밋된 파일)를 덮어쓰므로,
# 데이터 준비만 필요할 때는 이 스크립트를 쓴다.

from __future__ import annotations

import subprocess
import sys

from core.config import PROJECT_ROOT
from risk_defender.demo import (
    DEFAULT_DATA_PATH,
    DEFAULT_DATA_URL,
    DEFAULT_SYNTH_PATH,
    ensure_default_dataset,
)

GENERATOR_SCRIPT = PROJECT_ROOT / "scripts" / "generate_synth_population.py"


def prepare() -> None:
    """
    input : 없음
    output : 없음 (파일 생성)
    """
    print(f"[1/2] 원본 300명: {DEFAULT_DATA_PATH}")
    ensure_default_dataset(DEFAULT_DATA_URL)
    print("      준비됨")

    print(f"[2/2] 합성 30만 명: {DEFAULT_SYNTH_PATH}")
    if DEFAULT_SYNTH_PATH.exists():
        print("      이미 있음 (다시 만들려면 파일을 지우고 재실행)")
        return
    if not GENERATOR_SCRIPT.exists():
        raise FileNotFoundError(f"합성 생성 스크립트가 없습니다: {GENERATOR_SCRIPT}")
    subprocess.run([sys.executable, str(GENERATOR_SCRIPT)], check=True, cwd=PROJECT_ROOT)


if __name__ == "__main__":
    prepare()
