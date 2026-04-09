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


class AgentTask(BaseModel):
    agent: str
    task: str

class StageConfig(BaseModel):
    agents: list[AgentTask]
    batch_eligible: bool = False
    pass_forward: bool = True
    max_agents: int = 3

class WorkflowPlan(BaseModel):
    mode: Literal["workflow"]
    reasoning: str
    stages: list[StageConfig]
    completion_criteria: str


# Anti-hallucination Layer 0: Source anchoring
class SourcedClaim(BaseModel):
    claim: str
    source_type: Literal["VERIFIED", "HIGH_CONFIDENCE", "RECALLED"]
    source_document: Optional[str] = None
    source_page: Optional[int] = None
    source_quote: Optional[str] = None
    confidence: float
