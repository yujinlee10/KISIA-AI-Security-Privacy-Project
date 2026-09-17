from fuzzer_mutation import attackseed_generator
from llm_client import llm_request


def main():

    attack_prompts = attackseed_generator()

    age_prompt = attack_prompts[0]

    print("Attack Prompt:")
    print(age_prompt)
    print("------")

    response = llm_request(age_prompt)

    print("LLM Response:")
    print(response)

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


if __name__ == "__main__":
    main()
    


