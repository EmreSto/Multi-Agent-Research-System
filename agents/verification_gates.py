import re
from dataclasses import dataclass
from typing import Any, Literal

from config.agent_config import AGENT_CONFIG


@dataclass
class GateResult:
    passed: bool
    severity: Literal["error", "warning", "info"]
    gate_name: str
    message: str
    context: dict | None = None


#Gates

VERIFIED_TO_RECALLED_WARNING_RATIO = 3


def check_recalled_claims(call_result: dict) -> GateResult:
    summary = call_result.get("confidence_summary", {})
    agent = call_result.get("agent", "unknown")
    text = call_result.get("text", "")

    verified = summary.get("verified", 0)
    recalled = summary.get("recalled", 0)
    has_recalled = summary.get("has_recalled", False)

    regex_hit = bool(
        re.search(r"\[RECALLED\b|RECALLED\s*[—–-]", text, re.IGNORECASE)
    )

    if not has_recalled and not regex_hit:
        return GateResult(
            passed=True,
            severity="info",
            gate_name="recalled_claims",
            message="No RECALLED claims detected.",
        )

    excerpts = []
    for line in text.split("\n"):
        if re.search(r"\[RECALLED\b|RECALLED\s*[—–-]", line, re.IGNORECASE):
            clean = line.strip()
            if clean:
                excerpts.append(clean[:300])

    first_excerpt = excerpts[0] if excerpts else "(no line extracted)"
    total = verified + recalled

    if recalled > 0 and verified >= VERIFIED_TO_RECALLED_WARNING_RATIO * recalled:
        return GateResult(
            passed=True,
            severity="warning",
            gate_name="recalled_claims",
            message=(
                f"Agent '{agent}' has {recalled} RECALLED out of {total} total "
                f"claims ({verified} VERIFIED). Mostly grounded — review before "
                f"accepting. Excerpt: {first_excerpt}"
            ),
            context={
                "confidence_summary": summary,
                "recalled_excerpts": excerpts[:5],
            },
        )

    return GateResult(
        passed=False,
        severity="error",
        gate_name="recalled_claims",
        message=(
            f"Agent '{agent}' returned {recalled} RECALLED claim(s) with only "
            f"{verified} VERIFIED (out of {total} total). Pipeline halted for "
            f"source verification. Excerpt: {first_excerpt}"
        ),
        context={
            "confidence_summary": summary,
            "recalled_excerpts": excerpts[:5],
        },
    )


def check_domain_boundary(call_result: dict) -> GateResult:
    text = call_result.get("text", "")
    agent = call_result.get("agent", "unknown")
    if "[NOT MY DOMAIN]" in text:
        return GateResult(
            passed=False,
            severity="error",
            gate_name="domain_boundary",
            message=(
                f"Agent '{agent}' rejected query as outside its domain. "
                f"Response snippet: {text[:500]}"
            ),
            context={"text_snippet": text[:500]},
        )
    return GateResult(
        passed=True,
        severity="info",
        gate_name="domain_boundary",
        message="Domain boundary respected.",
    )


def validate_schema_completeness(data: Any, schema_cls) -> GateResult:
    try:
        schema_cls.model_validate(data)
        return GateResult(
            passed=True,
            severity="info",
            gate_name="schema_completeness",
            message=f"Data conforms to {schema_cls.__name__}.",
        )
    except Exception as e:
        return GateResult(
            passed=False,
            severity="error",
            gate_name="schema_completeness",
            message=f"Schema validation failed for {schema_cls.__name__}: {e}",
            context={"errors": str(e)},
        )


def validate_upstream_reference(downstream_text: str, upstream_text: str) -> GateResult:
    abbreviations = set(re.findall(r"\b[A-Z]{2,}\b", upstream_text))
    quoted = set(re.findall(r'"([^"]{3,50})"', upstream_text))
    latex_vars = set(re.findall(r"\\([a-zA-Z]+)\s*=", upstream_text))

    distinctive = abbreviations | quoted | latex_vars
    if not distinctive:
        return GateResult(
            passed=True,
            severity="info",
            gate_name="upstream_reference",
            message="Upstream text contained no distinctive tokens to check.",
        )

    hits = [t for t in distinctive if t in downstream_text]
    if not hits:
        return GateResult(
            passed=True,
            severity="warning",
            gate_name="upstream_reference",
            message=(
                f"Downstream response does not reference any distinctive tokens "
                f"from upstream output. Expected one of: {sorted(distinctive)[:5]}"
            ),
            context={"distinctive_tokens": sorted(distinctive)},
        )
    return GateResult(
        passed=True,
        severity="info",
        gate_name="upstream_reference",
        message=f"Downstream references upstream tokens: {hits[:3]}",
    )


def validate_routing_plan(routing_plan) -> GateResult:
    valid_agents = set(AGENT_CONFIG.keys()) - {"orchestrator"}

    for agent in routing_plan.agents:
        if agent not in valid_agents:
            return GateResult(
                passed=False,
                severity="error",
                gate_name="routing_plan",
                message=(
                    f"Unknown agent '{agent}' in routing plan. "
                    f"Valid agents: {sorted(valid_agents)}"
                ),
                context={"invalid_agent": agent, "valid_agents": sorted(valid_agents)},
            )

    return GateResult(
        passed=True,
        severity="info",
        gate_name="routing_plan",
        message="Routing plan valid.",
    )


def run_all_gates(*results: GateResult) -> GateResult:
    errors = [r for r in results if not r.passed]
    warnings = [r for r in results if r.passed and r.severity == "warning"]

    if errors:
        return GateResult(
            passed=False,
            severity="error",
            gate_name="aggregate",
            message="; ".join(f"[{r.gate_name}] {r.message}" for r in errors),
            context={"error_count": len(errors), "warning_count": len(warnings)},
        )
    if warnings:
        return GateResult(
            passed=True,
            severity="warning",
            gate_name="aggregate",
            message="; ".join(f"[{r.gate_name}] {r.message}" for r in warnings),
            context={"warning_count": len(warnings)},
        )
    return GateResult(
        passed=True,
        severity="info",
        gate_name="aggregate",
        message="All gates passed.",
    )


#Layer 5 helpers

def extract_equations(text: str) -> list[str]:
    equations = []

    display_math = re.findall(r"\$\$(.+?)\$\$", text, re.DOTALL)
    equations.extend(display_math)
    text_no_display = re.sub(r"\$\$.+?\$\$", "", text, flags=re.DOTALL)

    inline_math = re.findall(r"\$([^$\n]+?)\$", text_no_display)
    equations.extend(inline_math)

    equations.extend(re.findall(r"\\begin\{equation\*?\}(.+?)\\end\{equation\*?\}", text, re.DOTALL))
    equations.extend(re.findall(r"\\begin\{align\*?\}(.+?)\\end\{align\*?\}", text, re.DOTALL))
    equations.extend(re.findall(r"\\\[(.+?)\\\]", text, re.DOTALL))

    return [e.strip() for e in equations if e.strip()]


def extract_code_blocks(text: str, language: str = "python") -> list[str]:
    explicit = re.findall(rf"```{language}\n(.+?)```", text, re.DOTALL)
    if explicit:
        return [c.strip() for c in explicit]
    generic = re.findall(r"```(?:\w*)\n(.+?)```", text, re.DOTALL)
    return [c.strip() for c in generic]


def build_code_math_verification_prompt(equations: list[str], code: list[str]) -> str:
    equations_section = "\n\n".join(
        f"Equation {i + 1}:\n{eq}" for i, eq in enumerate(equations)
    )
    code_section = "\n\n".join(
        f"Code block {i + 1}:\n```python\n{c}\n```" for i, c in enumerate(code)
    )

    return (
        "You are performing Layer 5 code-math verification. Your job is to verify "
        "line-by-line that the provided Python code correctly implements the "
        "provided mathematical formulas.\n\n"
        "For each equation, identify the corresponding lines in the code and check:\n"
        "1. Sign conventions (positive/negative, add/subtract)\n"
        "2. Indexing (0-indexed vs 1-indexed, off-by-one errors)\n"
        "3. Boundary conditions (loop bounds, edge cases)\n"
        "4. Operator precedence (parentheses correctness)\n"
        "5. Variable name mapping (does code variable X correspond to the math symbol X?)\n\n"
        "Mark each finding as [VERIFIED] if the code correctly implements the equation, "
        "[HIGH_CONFIDENCE] if you see strong evidence but some details are unclear, "
        "or [RECALLED] if you cannot verify the mapping from the provided material alone.\n\n"
        "## Equations\n\n"
        f"{equations_section}\n\n"
        "## Code\n\n"
        f"{code_section}\n\n"
        "Report your findings equation by equation."
    )
