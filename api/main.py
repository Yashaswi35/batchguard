"""FastAPI service for BatchGuard.

  uvicorn api.main:app --reload
  POST /investigate                       {"batch_id": 92}
  GET  /investigations/{run_id}           summary, audit trail, approvals
  POST /investigations/{run_id}/approve   {"reviewer": "...", "decision": "APPROVED", "comment": "..."}
  GET  /health

Optional reviewer workflow (Power Automate):
  POWER_AUTOMATE_FLOW_URL  flow trigger URL, called when a run needs review
  PUBLIC_BASE_URL          public address of this API, sent so the flow can post the decision back
  APPROVAL_TOKEN           when set, /approve requires header X-Approval-Token with this value
"""
import hmac
import os
from pathlib import Path
from typing import Literal, Optional, Union

import httpx
from fastapi import FastAPI, Header, HTTPException
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


def review_message(inv):
    """Plain-text summary a reviewer reads inside the approval request."""
    s = inv.get("summary") or {}
    lines = [f"Batch {inv['batch_id']} on {inv['reactor_id']}: {inv['status']}",
             f"Reason: {inv['status_reason']}"]
    for d in inv.get("deviating_variables", [])[:4]:
        lines.append(f"Deviation: {d['name']} up to |z| {d['max_abs_z']:.1f} ({d['first_start']} to {d['last_end']})")
    lines.append(f"Probable cause: {s.get('probable_cause_equipment_id') or 'no equipment cause'}")
    if s.get("probable_cause"):
        lines.append(s["probable_cause"])
    for f in s.get("equipment_findings", []):
        lines.append(f"Finding: {f['equipment_id']}: {f['issue']} [{', '.join(f['source_ids'])}]")
    if s.get("regulatory_refs"):
        lines.append("Regulations: " + ", ".join(f"21 CFR {r['section']}" for r in s["regulatory_refs"]))
    for a in s.get("recommended_actions", []):
        lines.append(f"Action: {a}")
    if s.get("escalation_reason"):
        lines.append(f"Escalation: {s['escalation_reason']}")
    return "\n".join(lines)


def notify_reviewer(inv, flow_url, public_base, audit):
    """Start the Power Automate approval. A failed notification is logged, never fatal."""
    payload = dict(run_id=inv["run_id"], batch_id=inv["batch_id"], reactor_id=inv["reactor_id"],
                   status=inv["status"], title=f"BatchGuard: review deviation for batch {inv['batch_id']}",
                   details=review_message(inv),
                   approve_url=f"{public_base.rstrip('/')}/investigations/{inv['run_id']}/approve",
                   details_url=f"{public_base.rstrip('/')}/investigations/{inv['run_id']}")
    try:
        r = httpx.post(flow_url, json=payload, timeout=15)
        outcome = {"http_status": r.status_code}
    except httpx.HTTPError as e:
        outcome = {"error": type(e).__name__, "detail": str(e)[:200]}
    audit.log(inv["run_id"], "notify_reviewer", {"channel": "power_automate"}, outcome,
              model="power-automate", prompt_version=prompts.PROMPT_VERSION)
    return outcome


def create_app(llm_kind=None, lake=None, regs=None, audit_path=None, flow_url=None, public_base=None,
               approval_token=None):
    llm_kind = llm_kind or os.environ.get("BATCHGUARD_LLM", "gemini")
    lake = Path(lake or os.environ.get("BATCHGUARD_LAKE", C.LAKE))
    regs = Path(regs or C.REPO / "regs" / "cfr_21_part_211.json")
    flow_url = flow_url or os.environ.get("POWER_AUTOMATE_FLOW_URL")
    public_base = public_base or os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    approval_token = approval_token or os.environ.get("APPROVAL_TOKEN")
    audit = AuditLog(audit_path or C.DATA_DIR / "audit.db")
    llm = get_llm(llm_kind)
    graph = build_graph(GoldStore(lake), RegRetriever(regs), llm, audit)

    app = FastAPI(title="BatchGuard", version="0.2.0",
                  description="Governed deviation-investigation agent. Every summary requires human approval.")

    @app.get("/health")
    def health():
        return dict(status="ok", model=llm.name, prompt_version=prompts.PROMPT_VERSION, lake=str(lake),
                    reviewer_workflow="power_automate" if flow_url else "none",
                    approval_token_required=bool(approval_token))

    @app.post("/investigate", response_model=Investigation)
    def run_investigation(req: InvestigateRequest):
        inv = investigate(graph, req.batch_id)
        if flow_url and inv["status"] in REVIEWABLE:
            notify_reviewer(inv, flow_url, public_base, audit)
        return inv

    @app.get("/investigations/{run_id}")
    def get_investigation(run_id: str):
        inv = audit.get_investigation(run_id)
        if inv is None:
            raise HTTPException(404, f"No investigation {run_id}")
        return dict(investigation=inv, audit_trail=audit.trail(run_id), approvals=audit.approvals(run_id))

    @app.post("/investigations/{run_id}/approve")
    def approve(run_id: str, req: ApprovalRequest, x_approval_token: Optional[str] = Header(default=None)):
        if approval_token and not hmac.compare_digest(x_approval_token or "", approval_token):
            raise HTTPException(401, "Missing or invalid X-Approval-Token")
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


if os.environ.get("BATCHGUARD_AUTOCREATE", "1") == "1":
    from dotenv import load_dotenv

    load_dotenv(C.REPO / ".env")  # before create_app reads its settings
    app = create_app()
else:
    app = None
