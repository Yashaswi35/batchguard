"""FastAPI service for BatchGuard.

  uvicorn api.main:app --reload
  POST /investigate                       {"batch_id": 92}
  GET  /investigations/{run_id}           summary, audit trail, approvals
  POST /investigations/{run_id}/approve   {"reviewer": "...", "decision": "APPROVED", "comment": "..."}
  GET  /health
"""
import os
from pathlib import Path
from typing import Literal, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import prompts
from agent.audit import AuditLog
from agent.graph import build_graph, investigate
from agent.llm import get_llm
from agent.schemas import Investigation
from agent.tools import GoldStore, RegRetriever
from pipeline import config as C

REVIEWABLE = {"PENDING_APPROVAL", "ESCALATED"}


class InvestigateRequest(BaseModel):
    batch_id: Union[int, str] = Field(description="Batch number to investigate")


class ApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=2, description="Name or id of the qualified reviewer")
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = Field(min_length=5, description="Reason for the decision, required for the record")


def create_app(llm_kind=None, lake=None, regs=None, audit_path=None):
    llm_kind = llm_kind or os.environ.get("BATCHGUARD_LLM", "gemini")
    lake = Path(lake or os.environ.get("BATCHGUARD_LAKE", C.LAKE))
    regs = Path(regs or C.REPO / "regs" / "cfr_21_part_211.json")
    audit = AuditLog(audit_path or C.DATA_DIR / "audit.db")
    llm = get_llm(llm_kind)
    graph = build_graph(GoldStore(lake), RegRetriever(regs), llm, audit)

    app = FastAPI(title="BatchGuard", version="0.1.0",
                  description="Governed deviation-investigation agent. Every summary requires human approval.")

    @app.get("/health")
    def health():
        return dict(status="ok", model=llm.name, prompt_version=prompts.PROMPT_VERSION, lake=str(lake))

    @app.post("/investigate", response_model=Investigation)
    def run_investigation(req: InvestigateRequest):
        return investigate(graph, req.batch_id)

    @app.get("/investigations/{run_id}")
    def get_investigation(run_id: str):
        inv = audit.get_investigation(run_id)
        if inv is None:
            raise HTTPException(404, f"No investigation {run_id}")
        return dict(investigation=inv, audit_trail=audit.trail(run_id), approvals=audit.approvals(run_id))

    @app.post("/investigations/{run_id}/approve")
    def approve(run_id: str, req: ApprovalRequest):
        inv = audit.get_investigation(run_id)
        if inv is None:
            raise HTTPException(404, f"No investigation {run_id}")
        if inv["status"] not in REVIEWABLE:
            raise HTTPException(409, f"Status {inv['status']} does not require approval")
        if audit.approvals(run_id):
            raise HTTPException(409, "This investigation already has a recorded decision")
        audit.approve(run_id, req.reviewer, req.decision, req.comment)
        audit.log(run_id, "human_review", {"reviewer": req.reviewer},
                  {"decision": req.decision, "comment": req.comment}, model="human",
                  prompt_version=prompts.PROMPT_VERSION)
        return dict(run_id=run_id, decision=req.decision, reviewer=req.reviewer, approvals=audit.approvals(run_id))

    return app


app = create_app() if os.environ.get("BATCHGUARD_AUTOCREATE", "1") == "1" else None
