from pathlib import Path
from dotenv import load_dotenv
import os
import json
import requests

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"
SEED_PROMPT_FILE = BASE_DIR / "seedprompt.txt"
ATTACK_SEED_FILE = BASE_DIR / "attackseed.txt"

RESULT_DIR = BASE_DIR / "results"
RESULT_FILE = RESULT_DIR / "mutation_results.jsonl"


load_dotenv(ENV_FILE)


API_KEY = os.getenv("LLM_API_KEY")
API_BASE = os.getenv("LLM_API_BASE")
MODEL = os.getenv("LLM_MODEL")


print("API BASE:", API_BASE)
print("MODEL:", MODEL)

if API_KEY:
    print("API KEY: 정상적으로 불러왔습니다.")
else:
    print("API KEY: 불러오지 못했습니다.")

print("환경 변수 로드 완료.")
print("------")


def attackseed_generator():

    targets = [
        "AGE",
        "LOC",
        "OCCP",
        "EDU",
        "SEX",
        "INC"
    ]

    # attackseed.txt 읽기
    with open(ATTACK_SEED_FILE, "r", encoding="utf-8") as f:
        seed_prompt = f.read().strip()

    attack_prompts = []

    for target in targets:
        attack_prompt = seed_prompt.replace("{target}", target)
        attack_prompts.append(attack_prompt)

    return attack_prompts


attack_prompts = attackseed_generator()
print(attack_prompts)
for prompt in attack_prompts:
    print(prompt)