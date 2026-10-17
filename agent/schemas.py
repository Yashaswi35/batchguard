"""The JSON contract. DraftSummary is what the LLM may write. Investigation is what the API returns."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EquipmentFinding(BaseModel):
    equipment_id: str = Field(description="Instrument id exactly as given in candidates")
    issue: str = Field(description="One sentence describing the maintenance or calibration issue")
    source_ids: list[str] = Field(description="CAL- or WO- record ids exactly as given in the candidate's issues")


class RegulatoryRef(BaseModel):
    section: str = Field(description="Section number exactly as given in regulations, e.g. 211.68")
    relevance: str = Field(description="One sentence on why this requirement applies")


class DraftSummary(BaseModel):
    probable_cause_equipment_id: Optional[str] = Field(
        description="Candidate instrument id with a supporting record, or null if no equipment cause is supported")
    probable_cause: str = Field(description="Two sentences at most, grounded only in the context")
    equipment_findings: list[EquipmentFinding]
    regulatory_refs: list[RegulatoryRef]
    recommended_actions: list[str] = Field(description="Two to four concrete next steps for the investigator")
    confidence: Literal["high", "medium", "low"]
    escalation_reason: Optional[str] = Field(
        description="Set when records conflict or the evidence cannot support a conclusion, otherwise null")


class DeviatingVariable(BaseModel):
    name: str
    max_abs_z: float
    first_start: str
    last_end: str
    event_ids: list[str]


Status = Literal["NO_DEVIATION", "PENDING_APPROVAL", "ESCALATED", "REJECTED_INPUT"]


class Investigation(BaseModel):
    run_id: str
    batch_id: int
    reactor_id: Optional[str] = None
    deviation_detected: bool
    investigation_window: Optional[list[str]] = None
    deviating_variables: list[DeviatingVariable] = []
    summary: Optional[DraftSummary] = None
    status: Status
    status_reason: str
    verification_errors: list[str] = []
    attempts: int = 0
    model: str
    prompt_version: str
    created_at: str
