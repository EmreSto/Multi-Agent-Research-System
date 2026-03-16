from agents.orchestrator import execute_query


def main():
    while True:
        try:
            user_input = input("Enter a prompt (or 'exit' to quit):")
            if user_input.lower()== "exit":
                print("Exiting the research system. Goodbye!")
                break
            result = execute_query(user_input)
            if isinstance(result, dict):
                print(result["text"])
            elif isinstance(result, list):
                for item in result:
                    print(f"\n[{item['agent']}]")
                    print(item["text"])
        except Exception as e:
             print(f"Error: {e}")
            
if __name__ == "__main__":
    main()

