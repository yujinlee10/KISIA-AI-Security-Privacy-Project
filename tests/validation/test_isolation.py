# 정보 경계 테스트 — 실행 중 코드가 정답을 읽는 validation 패키지를 import하지 않는지 (D1 Part III)

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PACKAGES = ("orchestrator", "adapters", "attack", "ontology", "normalizer", "qi_extractor",
                    "target_llm", "fuzzer", "risk_defender", "core")


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(item, ast.Import):
            names.update(alias.name for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.module:
            names.add(item.module)
    return names


@pytest.mark.parametrize("package", RUNTIME_PACKAGES)
def test_runtime_package_does_not_import_validation(package):
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in sorted((PROJECT_ROOT / package).rglob("*.py"))
        if any(name == "validation" or name.startswith("validation.") for name in imported_modules(path))
    ]
    assert offenders == []
