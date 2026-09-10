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
    """
    input : attackseed.txt
    output : 1차 mutation축을 변경한 공격 프롬프트 리스트


    attackseed.txt 파일을 읽어 공격 프롬프트를 생성합니다.
    현재는 테스트용이기에 간단하게 작성하여
    문장의 {target} 부분을 targets리스트 안의 값들로 변경하여 attackseed 리스트를 생성합니다.
    """

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
    
    print("공격 프롬프트 1차 mutation 생성 완료.")

    return attack_prompts



def llm_request(attack_prompt):
    """
    input : attack_prompt
    output : LLM의 응답 결과

    LLM API에 공격 프롬프트를 전송하고 응답을 받아옵니다.
    """

    with open(SEED_PROMPT_FILE, "r", encoding="utf-8") as f:
        seed_prompt = f.read().strip()

    url = f"{API_BASE.rstrip('/')}/chat/completions"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": seed_prompt
            },
            {
                "role": "user",
                "content": attack_prompt
            }
        ],
        "temperature": 0.7
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    result = response.json()

    return result["choices"][0]["message"]["content"]


attack_prompts = attackseed_generator()
print(attack_prompts)

#for prompt in attack_prompts:
#    print(prompt)

