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


# Anti-hallucination Layer 0: Source anchoring
# Every claim must carry source metadata. Schema validation
# fails if required fields are missing — structural enforcement.
class SourcedClaim(BaseModel):
    claim: str
    source_type: Literal["VERIFIED", "HIGH_CONFIDENCE", "RECALLED"]
    source_document: Optional[str] = None
    source_page: Optional[int] = None
    source_quote: Optional[str] = None
    confidence: float
