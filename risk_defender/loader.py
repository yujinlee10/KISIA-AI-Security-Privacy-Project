# SynthPAI 데이터셋을 읽어서 우리 실험에 필요한 QI만 뽑는 파일

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import DEFAULT_QI_ORDER, normalize_qi_value, to_age_band


@dataclass(frozen=True)
class PersonaRecord:
    person_id: str
    username: str | None
    qis: dict[str, Any]
    raw_profile: dict[str, Any]
    source_texts: list[str]


def load_expanded_population(
    base_path: str | Path,
    synth_path: str | Path | None = None,
) -> list[PersonaRecord]:
    """원본 + 합성 프로필을 합쳐서 반환. synth_path가 없으면 원본만 반환."""
    records = load_population(base_path)
    if synth_path is not None:
        synth_path = Path(synth_path)
        if synth_path.exists():
            records = records + load_population(synth_path)
    return records


def load_population(path: str | Path) -> list[PersonaRecord]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"SynthPAI file not found: {path}")

    raw_items = _read_json_or_jsonl(path)
    records_by_id: dict[str, PersonaRecord] = {}

    for index, item in enumerate(raw_items):
        profile = _extract_profile(item)
        person_id = str(item.get("author") or item.get("person_id") or item.get("id") or f"pers{index + 1}")
        username = item.get("username") or profile.get("username")
        qis = _extract_qis(profile)
        texts = []
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)

        existing = records_by_id.get(person_id)
        if existing is None:
            records_by_id[person_id] = PersonaRecord(
                person_id=person_id,
                username=str(username) if username is not None else None,
                qis=qis,
                raw_profile=profile,
                source_texts=texts,
            )
        elif texts:
            records_by_id[person_id] = PersonaRecord(
                person_id=existing.person_id,
                username=existing.username,
                qis=existing.qis,
                raw_profile=existing.raw_profile,
                source_texts=[*existing.source_texts, *texts],
            )

    return list(records_by_id.values())


def _read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if all(isinstance(value, dict) for value in data.values()):
            return [
                {"author": key, "profile": value}
                for key, value in data.items()
            ]
        for key in ("profiles", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]

    raise ValueError(f"Unsupported SynthPAI JSON structure: {path}")


def _extract_profile(item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("profile", item)
    if not isinstance(profile, dict):
        raise ValueError(f"Cannot extract profile from item: {item}")
    return profile


def _extract_qis(profile: dict[str, Any]) -> dict[str, Any]:
    education = profile.get("education_categorized") or derive_education(profile.get("education"))
    raw_qis = {
        "AGE": to_age_band(profile.get("age")),
        "LOC": profile.get("city_country"),
        "OCCP": profile.get("occupation"),
        "EDU": education,
        "REL": profile.get("relationship_status"),
    }
    return {
        qi_type: normalize_qi_value(qi_type, raw_qis.get(qi_type))
        for qi_type in DEFAULT_QI_ORDER
        if normalize_qi_value(qi_type, raw_qis.get(qi_type)) is not None
    }


def derive_education(education: Any) -> str | None:
    if education is None:
        return None
    text = str(education).lower()
    if "doctorate" in text or "phd" in text:
        return "PhD"
    if "master" in text:
        return "Master's"
    if "bachelor" in text:
        return "College Degree"
    if "studying towards" in text:
        return "In College/HS Diploma"
    if "high school" in text:
        return "HS Diploma"
    return str(education)
