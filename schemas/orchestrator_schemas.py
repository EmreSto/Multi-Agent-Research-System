from pydantic import BaseModel
from typing import Literal, Optional

class RoutingPlan(BaseModel):
    mode: Literal["routing"]
    reasoning: str
    agents: list[str]
    sequence: str
    teacher_mode: Optional[Literal["internal_briefing", "external_teaching"]] = None
    pass_forward: bool
    synthesis_strategy: str
    completion_criteria: str


class RoutingPlanSimple(BaseModel):
    mode: Literal["simple"]
    agent: str
    reasoning: str


