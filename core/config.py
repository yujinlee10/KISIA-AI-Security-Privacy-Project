# 프로젝트 전역 설정(.env)을 읽어 불변(frozen) Settings 객체로 제공하는 파일
#
# 설계 원칙
#   1) import 시점에는 아무 부작용이 없다 (.env 로딩·print·os.environ 수정 없음).
#      .env는 get_settings()를 처음 호출할 때만 읽는다.
#   2) Settings.from_env(dict)로 환경을 주입할 수 있다.
#      실제 .env 없이도 테스트에서 원하는 값으로 검증 가능하다.
#   3) API 키는 repr·로그·오류 메시지 어디에도 노출되지 않는다.
#   4) 환경변수 이름은 fuzzer/llm_client.py(한재이 파트)와 동일하게 유지한다.
#        LLM_API_KEY / LLM_API_BASE / LLM_MODEL
#      같은 값을 쓰면 Fuzzer·Target·Extractor가 같은 모델을 호출하게 된다.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values


# ──────────────────────────────────────────────────────────────
# 경로 · 기본값
# ──────────────────────────────────────────────────────────────

# core/config.py → core/ → 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# PoC §3.2: 응답 토큰 500에서 한국어 응답이 중간에 잘려 QI 추출이 실패한 기록이 있어 1200으로 상향했다.
# fuzzer/llm_client.py의 512는 Mutation 문장 생성용이라 짧아도 되지만, Target 응답은 길어질 수 있다.
DEFAULT_MAX_TOKENS = 1200

# fuzzer/llm_client.py의 timeout=60과 동일하게 맞춘다.
DEFAULT_TIMEOUT_SEC = 60.0

# 일시적 오류(429·5xx·timeout)에 대한 재시도 횟수. 첫 시도는 포함하지 않는다.
DEFAULT_MAX_RETRIES = 3

# 호출 사이 최소 간격(초). 0이면 제한 없음.
# PoC에서는 30 RPM 제한 때문에 2.2초를 썼다. 환경마다 다르므로 .env로만 지정한다.
DEFAULT_MIN_INTERVAL_SEC = 0.0

# Anthropic Messages API 버전 헤더. fuzzer/llm_client.py와 동일.
ANTHROPIC_VERSION = "2023-06-01"

# 필수 환경변수. 이름은 fuzzer 파트와 반드시 같아야 한다.
REQUIRED_ENV_VARS = ("LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL")


# ──────────────────────────────────────────────────────────────
# 예외
# ──────────────────────────────────────────────────────────────

class ConfigError(ValueError):
    """
    필수 설정 누락이나 형식 오류를 나타낸다.
    메시지에는 변수 '이름'만 넣고 '값'은 절대 넣지 않는다 (키 노출 방지).
    """


# ──────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    """
    llm_api_key
    → monogpt API 키. repr=False라 print(settings)에도 나오지 않는다.
    llm_api_base
    → monogpt base URL. 끝의 '/'는 제거해서 저장한다.
    llm_model
    → 호출할 모델 ID. Fuzzer·Target·Extractor 통일 기준값.
    llm_max_tokens
    → 응답 최대 토큰 (기본 1200)
    llm_timeout_sec
    → HTTP 요청 timeout 초 (기본 60)
    llm_max_retries
    → 일시적 오류 재시도 횟수 (기본 3, 첫 시도 제외)
    llm_min_interval_sec
    → 호출 사이 최소 간격 초 (기본 0 = 제한 없음)
    """
    llm_api_key: str = field(repr=False)
    llm_api_base: str
    llm_model: str
    llm_max_tokens: int = DEFAULT_MAX_TOKENS
    llm_timeout_sec: float = DEFAULT_TIMEOUT_SEC
    llm_max_retries: int = DEFAULT_MAX_RETRIES
    llm_min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC

    @classmethod
    def from_env(cls, env: Mapping[str, str | None]) -> "Settings":
        """
        input : env (환경변수 이름 → 값 매핑. os.environ이나 테스트용 dict)
        output : Settings

        매핑에서 필요한 값만 읽어 검증한 뒤 Settings를 만든다.
        빈 문자열은 '설정되지 않음'으로 취급한다 (.env에 'LLM_API_KEY=' 처럼 비워둔 경우).
        """
        missing = [name for name in REQUIRED_ENV_VARS if not _clean(env.get(name))]
        if missing:
            raise ConfigError(
                f"필수 환경변수가 비어 있습니다: {', '.join(missing)}. "
                f".env.example을 복사해 .env를 만들고 값을 채우세요."
            )

        api_base = _clean(env.get("LLM_API_BASE")).rstrip("/")
        if not api_base.startswith(("http://", "https://")):
            raise ConfigError("LLM_API_BASE는 http:// 또는 https://로 시작해야 합니다.")

        return cls(
            llm_api_key=_clean(env.get("LLM_API_KEY")),
            llm_api_base=api_base,
            llm_model=_clean(env.get("LLM_MODEL")),
            llm_max_tokens=_parse_positive_int(env, "LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            llm_timeout_sec=_parse_positive_float(env, "LLM_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC),
            llm_max_retries=_parse_non_negative_int(env, "LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES),
            llm_min_interval_sec=_parse_non_negative_float(
                env, "LLM_MIN_INTERVAL_SEC", DEFAULT_MIN_INTERVAL_SEC
            ),
        )

    def describe(self) -> dict[str, Any]:
        """
        input : 없음
        output : dict (로그·실행 설정 기록용, API 키 제외)

        D1은 모든 run에 model·설정 버전을 기록하도록 요구한다.
        이 메서드 결과를 run_config 로그에 그대로 남기면 되며, 키는 '설정 여부'만 남긴다.
        """
        return {
            "llm_api_base": self.llm_api_base,
            "llm_model": self.llm_model,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_timeout_sec": self.llm_timeout_sec,
            "llm_max_retries": self.llm_max_retries,
            "llm_min_interval_sec": self.llm_min_interval_sec,
            "llm_api_key_set": bool(self.llm_api_key),
        }


# ──────────────────────────────────────────────────────────────
# 로딩 진입점
# ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def get_settings(env_file: str | Path | None = None) -> Settings:
    """
    input : env_file (읽을 .env 경로. None이면 프로젝트 루트의 .env)
    output : Settings

    .env 파일 값과 현재 셸의 환경변수를 합쳐 Settings를 만든다.
    같은 이름이 둘 다 있으면 셸 환경변수가 우선한다
    (fuzzer의 load_dotenv 기본 동작과 같은 우선순위).

    dotenv_values()는 os.environ을 수정하지 않으므로 다른 모듈에 부작용이 없다.
    결과는 캐시되며, 테스트에서 다시 읽으려면 get_settings.cache_clear()를 호출한다.
    """
    path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE

    merged: dict[str, str | None] = {}
    if path.exists():
        merged.update(dotenv_values(path))
    merged.update(os.environ)

    return Settings.from_env(merged)


# ──────────────────────────────────────────────────────────────
# 내부 파싱 함수
# ──────────────────────────────────────────────────────────────

def _clean(value: str | None) -> str:
    """None·공백을 빈 문자열로 통일하고 앞뒤 공백을 제거한다."""
    return (value or "").strip()


def _parse_positive_int(env: Mapping[str, str | None], name: str, default: int) -> int:
    raw = _clean(env.get(name))
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}는 정수여야 합니다.") from None
    if value <= 0:
        raise ConfigError(f"{name}는 1 이상이어야 합니다.")
    return value


def _parse_non_negative_int(env: Mapping[str, str | None], name: str, default: int) -> int:
    raw = _clean(env.get(name))
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}는 정수여야 합니다.") from None
    if value < 0:
        raise ConfigError(f"{name}는 0 이상이어야 합니다.")
    return value


def _parse_positive_float(env: Mapping[str, str | None], name: str, default: float) -> float:
    raw = _clean(env.get(name))
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}는 숫자여야 합니다.") from None
    if value <= 0:
        raise ConfigError(f"{name}는 0보다 커야 합니다.")
    return value


def _parse_non_negative_float(env: Mapping[str, str | None], name: str, default: float) -> float:
    raw = _clean(env.get(name))
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}는 숫자여야 합니다.") from None
    if value < 0:
        raise ConfigError(f"{name}는 0 이상이어야 합니다.")
    return value
