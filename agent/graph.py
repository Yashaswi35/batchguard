"""LangGraph state machine for one deviation investigation.

intake -> detect -> equipment_check -> retrieve_regs -> draft -> verify -> human_review
                 \\-> finalize (no deviation)                      \\-> draft (retry once) -> escalate
"""
import json
import uuid
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from . import prompts
from .audit import AuditLog, now
from .schemas import DeviatingVariable, DraftSummary, Investigation
from .tools import GoldStore, RegRetriever, build_reg_query
from .verify import verify

MAX_ATTEMPTS = 2


class State(TypedDict, total=False):
    run_id: str
    batch_id: Any
    batch: Optional[dict]
    deviation: list
    findings: dict
    regulations: list
    draft: Optional[dict]
    errors: list
    attempts: int
    result: dict


def build_graph(store: GoldStore, retriever: RegRetriever, llm, audit: AuditLog):
    meta = dict(model=llm.name, prompt_version=prompts.PROMPT_VERSION)

    def record(state, node, inputs, outputs):
        audit.log(state["run_id"], node, inputs, outputs, **meta)

    def finish(state, status, reason, summary=None):
        b = state.get("batch") or {}
        inv = Investigation(
            run_id=state["run_id"], batch_id=int(state["batch_id"]) if str(state["batch_id"]).lstrip("-").isdigit()
            else -1, reactor_id=b.get("reactor_id"), deviation_detected=bool(state.get("deviation")),
            investigation_window=b.get("window") if state.get("deviation") else None,
            deviating_variables=[DeviatingVariable(**{k: d[k] for k in DeviatingVariable.model_fields})
                                 for d in state.get("deviation", [])],
            summary=summary, status=status, status_reason=reason,
            verification_errors=state.get("errors", []), attempts=state.get("attempts", 0),
            model=llm.name, prompt_version=prompts.PROMPT_VERSION, created_at=now())
        record(state, "finalize", {"status": status}, {"status": status, "reason": reason})
        audit.save_investigation(inv)
        return {"result": inv.model_dump()}

    def intake(state):
        bid = state["batch_id"]
        batch = store.get_batch(int(bid)) if isinstance(bid, int) or str(bid).isdigit() else None
        record(state, "intake", {"batch_id": bid}, {"found": batch is not None})
        return {"batch": batch, "attempts": 0, "errors": []}

    def detect(state):
        dev = store.get_deviation(int(state["batch_id"]))
        record(state, "detect", {"batch_id": state["batch_id"]},
               {"deviating_variables": [d["name"] for d in dev]})
        return {"deviation": dev}

    def equipment_check(state):
        f = store.get_equipment_findings(int(state["batch_id"]))
        record(state, "equipment_check", {"batch_id": state["batch_id"]},
               {"candidates_with_issues": [c["equipment_id"] for c in f["candidates"] if c["issues"]],
                "allowed_source_ids": f["allowed_source_ids"], "untrusted_notes": len(f["untrusted_notes"])})
        return {"findings": f}

    def retrieve_regs(state):
        q = build_reg_query(state["deviation"], state["findings"])
        regs = retriever.search(q, k=3)
        record(state, "retrieve_regs", {"query": q}, {"sections": [r["section"] for r in regs]})
        return {"regulations": regs}

    def draft(state):
        user = prompts.build_user_prompt(state["batch"], state["deviation"], state["findings"], state["regulations"])
        if state.get("errors"):
            user += "\n\nYour previous draft failed verification. Fix exactly these problems:\n- " + \
                    "\n- ".join(state["errors"])
        attempts = state.get("attempts", 0) + 1
        try:
            raw = llm.generate(prompts.SYSTEM, user)
            d = DraftSummary.model_validate_json(raw).model_dump()
            errs = []
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            d, errs = None, [f"output did not match the DraftSummary schema: {str(e)[:200]}"]
        record(state, "draft", {"attempt": attempts, "prompt_chars": len(user)}, {"draft": d, "errors": errs})
        return {"draft": d, "attempts": attempts, "errors": errs}

    def verify_node(state):
        if state.get("draft") is None:
            return {}
        errs = verify(DraftSummary(**state["draft"]), state["findings"], state["regulations"])
        record(state, "verify", {"attempt": state["attempts"]}, {"errors": errs})
        return {"errors": errs}

    def human_review(state):
        s = DraftSummary(**state["draft"])
        if s.escalation_reason:
            return finish(state, "ESCALATED", f"Model requested escalation: {s.escalation_reason}", s)
        return finish(state, "PENDING_APPROVAL", "Draft verified. Awaiting QA approval.", s)

    def escalate(state):
        s = DraftSummary(**state["draft"]) if state.get("draft") else None
        return finish(state, "ESCALATED", f"Draft failed verification after {state['attempts']} attempts.", s)

    def reject(state):
        return finish(state, "REJECTED_INPUT", f"Batch {state['batch_id']!r} not found in batch records.")

    def no_deviation(state):
        return finish(state, "NO_DEVIATION", "No process variable left the golden-batch envelope at |z| >= 20.")

    g = StateGraph(State)
    for name, fn in [("intake", intake), ("detect", detect), ("equipment_check", equipment_check),
                     ("retrieve_regs", retrieve_regs), ("draft", draft), ("verify", verify_node),
                     ("human_review", human_review), ("escalate", escalate), ("reject", reject),
                     ("no_deviation", no_deviation)]:
        g.add_node(name, fn)
    g.set_entry_point("intake")
    g.add_conditional_edges("intake", lambda s: "detect" if s["batch"] else "reject")
    g.add_conditional_edges("detect", lambda s: "equipment_check" if s["deviation"] else "no_deviation")
    g.add_edge("equipment_check", "retrieve_regs")
    g.add_edge("retrieve_regs", "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges("verify", lambda s: "human_review" if not s["errors"]
                            else ("draft" if s["attempts"] < MAX_ATTEMPTS else "escalate"))
    for end in ["human_review", "escalate", "reject", "no_deviation"]:
        g.add_edge(end, END)
    return g.compile()


def investigate(app, batch_id):
    run_id = str(uuid.uuid4())
    return app.invoke({"run_id": run_id, "batch_id": batch_id})["result"]
