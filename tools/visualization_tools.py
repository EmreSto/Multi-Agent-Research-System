import json
import uuid

from tools.registry import ToolRegistry


CREATE_VISUALIZATION_SCHEMA = {
    "name": "create_visualization",
    "description": (
        "Create an interactive visualization for the user. The visualization is "
        "rendered inline in the chat UI. Use for Plotly charts, HTML widgets, "
        "code exercises, step-through walkthroughs, or side-by-side comparisons. "
        "Call this when visualizing data, demonstrating an algorithm step, or "
        "asking the user to interact with a figure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "viz_type": {
                "type": "string",
                "enum": [
                    "plotly_chart",
                    "interactive_widget",
                    "code_exercise",
                    "step_through",
                    "comparison",
                ],
                "description": (
                    "Type of visualization. 'plotly_chart' for charts with a "
                    "Plotly figure spec, 'interactive_widget' for HTML/JS widgets, "
                    "'code_exercise' for code snippets the user fills in, "
                    "'step_through' for sequential walkthroughs, 'comparison' for "
                    "side-by-side views."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short title shown above the visualization.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence description of what the visualization shows.",
            },
            "spec": {
                "type": "object",
                "description": (
                    "The visualization payload. For plotly_chart: a full Plotly "
                    "figure spec with 'data' and 'layout'. For interactive_widget: "
                    "HTML/JS source. For code_exercise: an object with 'pre_code', "
                    "'blank', 'post_code', 'language' fields. Content depends on "
                    "viz_type."
                ),
            },
            "interaction_prompt": {
                "type": "string",
                "description": (
                    "Optional question or call-to-action shown to the user after "
                    "they view the visualization."
                ),
            },
        },
        "required": ["viz_type", "title", "spec"],
    },
}


def create_visualization(tool_input: dict) -> str:
    viz_id = str(uuid.uuid4())
    return json.dumps({
        "viz_id": viz_id,
        "viz_type": tool_input.get("viz_type"),
        "error": "renderer_unavailable",
        "message": (
            "No visualization renderer is available in this context (CLI mode). "
            "Describe the visualization in text — what the user would see, "
            "the axes, the relationships — with enough detail that they can "
            "picture it."
        ),
    })


def register_visualization_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="create_visualization",
        schema=CREATE_VISUALIZATION_SCHEMA,
        handler=create_visualization,
        category="visualization",
    )
