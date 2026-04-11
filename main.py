import asyncio

from agents.base_agent import call_agent
from agents.orchestrator import check_simple_mode, execute_query
from agents.verification_gates import GateResult
from tools import ToolRegistry
from tools.research_tools import register_research_tools
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from time import time
from pathlib import Path
from tools.retrieval_tools import register_retrieval_tools
from tools.visualization_tools import register_visualization_tools

console = Console()

registry = ToolRegistry()
register_research_tools(registry)
register_retrieval_tools(registry)
register_visualization_tools(registry)


async def cli_checkpoint_fn(
    stage_idx: int,
    stage_results: list[dict],
    gate_summary: GateResult,
) -> bool:
    console.print(f"\n[bold cyan]=== Stage {stage_idx + 1} complete ===[/bold cyan]\n")

    for r in stage_results:
        console.print(Panel(
            Markdown(r.get("text", "")),
            title=f"[bold]{r.get('agent', 'unknown')}[/bold]",
            subtitle=f"${r.get('cost', 0):.4f} | {r.get('latency', 0):.1f}s",
        ))

    if gate_summary.severity == "warning":
        console.print(f"\n[yellow]Gate warnings:[/yellow] {gate_summary.message}")
    elif gate_summary.severity == "error":
        console.print(f"\n[red]Gate errors:[/red] {gate_summary.message}")

    response = await asyncio.to_thread(
        input, f"\nApprove stage {stage_idx + 1} and continue? [y/n]: "
    )
    return response.strip().lower().startswith("y")

def save_response_to_file(response):
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    timestamp = int(time())

    if isinstance(response, dict):
        agent_name = response["agent"]
        filename = output_dir / f"{timestamp}_{agent_name}.md"
        with open(filename, "w") as f:
            f.write(f"# {agent_name}\n")
            f.write(f"**Cost:** ${response['cost']:.4f} | **Latency:** {response['latency']:.1f}s\n\n---\n\n")
            f.write(response["text"])

    elif isinstance(response, list):
        filename = output_dir / f"{timestamp}_multi_agent.md"
        with open(filename, "w") as f:
            for item in response:
                f.write(f"# {item['agent']}\n")
                f.write(f"**Cost:** ${item['cost']:.4f} | **Latency:** {item['latency']:.1f}s\n\n---\n\n")
                f.write(item["text"])
                f.write("\n\n---\n\n")

    console.print(f"[dim]Saved to {filename}[/dim]")

def main():
    agent_histories = {}

    while True:
        try:
            user_input = input("\nEnter a prompt or 'exit' to quit: ")
            if user_input.lower() == "exit":
                console.print("Exiting the research system. Goodbye!")
                break

            if user_input.startswith("@"):
                query, agent = check_simple_mode(user_input)
                history = agent_histories.get(agent, [])
                result = call_agent(agent, query, history, registry=registry)

                while True:
                    console.print(Panel(
                        Markdown(result["text"]),
                        title=f"[bold]{result['agent']}[/bold]",
                        subtitle=f"${result['cost']:.4f} | {result['latency']:.1f}s",))
                    save_response_to_file(result)
                    agent_histories[agent] = result["history"]

                    follow_up = input(f"\n[{agent}] > ")
                    if follow_up.lower() == "exit":
                        break

                    result = call_agent(agent, follow_up, agent_histories[agent], registry=registry)

            else:
                result = execute_query(
                    user_input,
                    registry=registry,
                    checkpoint_fn=cli_checkpoint_fn,
                )
                if isinstance(result, dict):
                    console.print(Panel(
                        Markdown(result["text"]),
                        title=f"[bold]{result['agent']}[/bold]",
                        subtitle=f"${result['cost']:.4f} | {result['latency']:.1f}s",))

                    agent_histories[result["agent"]] = result.get("history", [])

                elif isinstance(result, list):
                    for item in result:
                        console.print(Panel(
                            Markdown(item["text"]),
                            title=f"[bold]{item['agent']}[/bold]",
                            subtitle=f"${item['cost']:.4f} | {item['latency']:.1f}s",))

                        agent_histories[item["agent"]] = item.get("history", [])
                save_response_to_file(result)

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    main()
