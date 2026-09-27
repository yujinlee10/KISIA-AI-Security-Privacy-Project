from fuzzer_mutation import attackseed_generator, generate_next_mutation
from llm_client import llm_request
from k_value_loader import load_k_value
from feedback import evaluate_feedback, select_mutation_strategy


def main():

    # Turn 0
    # 초기 공격 프롬프트 생성
    attack_prompts = attackseed_generator()

    initial_prompt = attack_prompts[0]

    print("Initial Prompt:")
    print(initial_prompt)
    print("------")

    # Mutation LLM을 통해 PROMPT1 생성
    prompt1 = llm_request(initial_prompt)

    print("PROMPT1:")
    print(prompt1)
    print("------")


    # Turn 1
    # 테스트용 CSV에서 K1 읽기
    k1 = load_k_value(1)


    # K1 -> Feedback
    feedback1 = evaluate_feedback(
        k_before=k1["k_before"],
        k_after=k1["k_after"],
        risk_gain=k1["risk_gain"],
        threshold=5,
    )

    # Feedback -> Strategy
    strategy1 = select_mutation_strategy(feedback1)

    print("Feedback1:")
    print(feedback1)

    print("Strategy1:")
    print(strategy1)
    print("------")


    # PROMPT1 + Strategy1 -> Mutation Instruction
    mutation_instruction = generate_next_mutation(
        previous_prompt=prompt1,
        strategy=strategy1,
    )

    if mutation_instruction is None:
        print("Branch aborted.")
        return

    print("Mutation Instruction:")
    print(mutation_instruction)
    print("------")


    # Mutation LLM을 통해 PROMPT2 생성
    prompt2 = llm_request(mutation_instruction)

    print("PROMPT2:")
    print(prompt2)


if __name__ == "__main__":
    main()