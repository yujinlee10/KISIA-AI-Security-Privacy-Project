from fuzzer_mutation import attackseed_generator, generate_next_mutation
from llm_client import llm_request
from k_value_loader import load_k_value
from feedback import evaluate_feedback, select_mutation_strategy
from trace_logger import log_turn


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

    # Turn 0 로그 저장
    log_turn(
        turn=0,
        output_prompt=prompt1,
    )

    max_turns = 3  # 최대 Turn 수 설정

    # Turn 1 ~ Turn 4 반복
    current_prompt = prompt1

    for turn in range(1, max_turns + 1):
        print(f"=== Turn {turn} ===")

        # 테스트용 CSV에서 K 읽기
        k = load_k_value(turn)

        # K -> Feedback
        feedback = evaluate_feedback(
            k_before=k["k_before"],
            k_after=k["k_after"],
            risk_gain=k["risk_gain"],
            threshold=5,
        )

        # Feedback -> Strategy
        strategy = select_mutation_strategy(feedback)

        print(f"Feedback{turn}:")
        print(feedback)

        print(f"Strategy{turn}:")
        print(strategy)
        print("------")

        # 직전 Prompt + Strategy -> Mutation Instruction
        mutation_instruction = generate_next_mutation(
            previous_prompt=current_prompt,
            strategy=strategy,
        )

        if mutation_instruction is None:
            # abort도 실행 결과이므로 Turn 로그는 남기고, output_prompt만 None으로 기록
            log_turn(
                turn=turn,
                input_prompt=current_prompt,
                k_before=k["k_before"],
                k_after=k["k_after"],
                strategy=strategy,
                output_prompt=None,
            )
            print("Branch aborted.")
            return

        print(f"Mutation Instruction {turn}:")
        print(mutation_instruction)
        print("------")

        # Mutation LLM을 통해 다음 Prompt 생성
        next_prompt = llm_request(mutation_instruction)

        print(f"PROMPT{turn + 1}:")
        print(next_prompt)
        print("------")

        # Turn 로그 저장 (input_prompt = 직전 turn의 output_prompt, output_prompt = 새로 생성된 prompt)
        log_turn(
            turn=turn,
            input_prompt=current_prompt,
            k_before=k["k_before"],
            k_after=k["k_after"],
            strategy=strategy,
            output_prompt=next_prompt,
        )

        # 다음 turn의 input_prompt로 사용
        current_prompt = next_prompt


if __name__ == "__main__":
    main()