# core/config.py 단위 테스트
# 실제 .env나 셸 환경변수에 의존하지 않도록 dict 주입과 임시 파일만 사용한다.

from __future__ import annotations

import os

import pytest

from core.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MIN_INTERVAL_SEC,
    DEFAULT_TIMEOUT_SEC,
    ConfigError,
    Settings,
    get_settings,
)

FAKE_KEY = "sk-test-SECRET-should-never-leak"

VALID_ENV = {
    "LLM_API_KEY": FAKE_KEY,
    "LLM_API_BASE": "https://monogpt.example.com/",
    "LLM_MODEL": "test-model",
}


# ── from_env: 정상 경로 ────────────────────────────────────

def test_from_env_reads_required_values_and_applies_defaults():
    settings = Settings.from_env(VALID_ENV)

    assert settings.llm_api_key == FAKE_KEY
    assert settings.llm_model == "test-model"
    assert settings.llm_max_tokens == DEFAULT_MAX_TOKENS
    assert settings.llm_timeout_sec == DEFAULT_TIMEOUT_SEC
    assert settings.llm_max_retries == DEFAULT_MAX_RETRIES
    assert settings.llm_min_interval_sec == DEFAULT_MIN_INTERVAL_SEC


def test_from_env_strips_trailing_slash_from_api_base():
    settings = Settings.from_env(VALID_ENV)
    assert settings.llm_api_base == "https://monogpt.example.com"


def test_from_env_reads_optional_overrides():
    env = {
        **VALID_ENV,
        "LLM_MAX_TOKENS": "2000",
        "LLM_TIMEOUT_SEC": "30",
        "LLM_MAX_RETRIES": "0",
        "LLM_MIN_INTERVAL_SEC": "2.2",
    }
    settings = Settings.from_env(env)

    assert settings.llm_max_tokens == 2000
    assert settings.llm_timeout_sec == 30.0
    assert settings.llm_max_retries == 0
    assert settings.llm_min_interval_sec == 2.2


def test_settings_is_immutable():
    settings = Settings.from_env(VALID_ENV)
    with pytest.raises(Exception):
        settings.llm_model = "other"  # type: ignore[misc]


# ── from_env: 오류 경로 ────────────────────────────────────

@pytest.mark.parametrize("missing", ["LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL"])
def test_missing_required_var_raises_with_name(missing):
    env = {k: v for k, v in VALID_ENV.items() if k != missing}
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    assert missing in str(exc.value)


def test_empty_string_is_treated_as_missing():
    env = {**VALID_ENV, "LLM_API_KEY": "   "}
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    assert "LLM_API_KEY" in str(exc.value)


def test_api_base_without_scheme_is_rejected():
    env = {**VALID_ENV, "LLM_API_BASE": "monogpt.example.com"}
    with pytest.raises(ConfigError):
        Settings.from_env(env)


@pytest.mark.parametrize(
    "name, value",
    [
        ("LLM_MAX_TOKENS", "abc"),
        ("LLM_MAX_TOKENS", "0"),
        ("LLM_TIMEOUT_SEC", "-1"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_MIN_INTERVAL_SEC", "x"),
    ],
)
def test_invalid_optional_values_raise(name, value):
    with pytest.raises(ConfigError):
        Settings.from_env({**VALID_ENV, name: value})


# ── 비밀값 비노출 ───────────────────────────────────────────

def test_repr_does_not_contain_api_key():
    settings = Settings.from_env(VALID_ENV)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_describe_excludes_api_key():
    described = Settings.from_env(VALID_ENV).describe()
    assert FAKE_KEY not in str(described)
    assert described["llm_api_key_set"] is True


def test_error_message_does_not_contain_values():
    env = {**VALID_ENV, "LLM_API_BASE": "not-a-url-" + FAKE_KEY}
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    assert FAKE_KEY not in str(exc.value)


# ── get_settings: .env 파일 로딩 ───────────────────────────

@pytest.fixture
def clean_environ(monkeypatch):
    """셸에 실제 LLM_* 값이 있어도 테스트가 영향받지 않도록 지운다."""
    for name in list(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_env(path, values):
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")


def test_get_settings_loads_env_file(tmp_path, clean_environ):
    env_file = tmp_path / ".env"
    _write_env(env_file, VALID_ENV)

    settings = get_settings(env_file)
    assert settings.llm_model == "test-model"


def test_shell_environment_overrides_env_file(tmp_path, clean_environ, monkeypatch):
    env_file = tmp_path / ".env"
    _write_env(env_file, VALID_ENV)
    monkeypatch.setenv("LLM_MODEL", "shell-model")

    settings = get_settings(env_file)
    assert settings.llm_model == "shell-model"


def test_get_settings_does_not_modify_os_environ(tmp_path, clean_environ):
    env_file = tmp_path / ".env"
    _write_env(env_file, VALID_ENV)

    get_settings(env_file)
    assert "LLM_API_KEY" not in os.environ


def test_missing_env_file_and_no_shell_vars_raises(tmp_path, clean_environ):
    with pytest.raises(ConfigError):
        get_settings(tmp_path / "does_not_exist.env")
