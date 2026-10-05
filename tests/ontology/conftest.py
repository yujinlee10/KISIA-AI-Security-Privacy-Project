# ontology 테스트 공용 준비물: tests/orchestrator/sample_data.py의 8명으로 만든 색인
#
#   id  나이  성별  거주지   출생지   직업(ISCO)              학력       소득       관계
#   p1  34   남   Zurich   Berlin   software engineer 2512  석사       high       single
#   p2  31   여   Zurich   Zurich   nurse 2221              학사       middle     single
#   p3  37   남   Zurich   Geneva   chef 3434               학사       middle     married
#   p4  45   여   Geneva   Geneva   software engineer 2512  박사       very high  married
#   p5  33   남   Geneva   Munich   software engineer 2512  석사       high       divorced
#   p6  28   여   Berlin   Berlin   software engineer 2512  석사       high       single
#   p7  52   여   Berlin   Munich   nurse 2221              고졸       low        widowed
#   p8  39   남   Munich   Berlin   teacher 23              학사       middle     married

from __future__ import annotations

import pytest

from ontology.index import PopulationIndex
from tests.orchestrator.sample_data import RAW_PROFILES


@pytest.fixture(scope="session")
def index() -> PopulationIndex:
    return PopulationIndex.build(list(RAW_PROFILES.values()))


@pytest.fixture(scope="session")
def ontology(index):
    return index.ontology


@pytest.fixture(scope="session")
def parser(index):
    return index.parser
