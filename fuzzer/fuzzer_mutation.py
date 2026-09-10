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
    claude-code api 규격을 기준으로 작성 되었습니다.
    """
    with open(SEED_PROMPT_FILE, "r", encoding="utf-8") as f:
        seed_prompt = f.read().strip()

    url = f"{API_BASE.rstrip('/')}/v1/messages"

    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "max_tokens": 512,

        "system": seed_prompt,

        "messages": [
            {
                "role": "user",
                "content": attack_prompt
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    print("STATUS:", response.status_code)
    print("RAW RESPONSE:", response.text)

    response.raise_for_status()

    result = response.json()

    return result["content"][0]["text"]

#for prompt in attack_prompts:
#    print(prompt)

"""
실행을 위한 코드로 attackseed_generator() 함수를 통해 생성된 공격 프롬프트를 llm_request() 함수에 전달하여 LLM의 응답을 받아오는 예시입니다. 
다만 테스트용으로는 일단 먼저 하나의 응답만 받아오도록 주석 처리하였습니다.
"""
# for seed in attack_seeds:

#     target = seed["target"]
#     attack_prompt = seed["prompt"]

#     mutated_prompt = llm_request(attack_prompt)

#     print("TARGET:", target)
#     print("ORIGINAL:", attack_prompt)
#     print("MUTATED:", mutated_prompt)
#     print("------")

attack_prompt = attackseed_generator()
age_prompt = attack_prompt[0]  # AGE에 대한 공격 프롬프트 선택

print("Attack Prompt:")
print(age_prompt)
print("------")

response = llm_request(age_prompt)

print("LLM Response:")
print(response)