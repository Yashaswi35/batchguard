"""Programmatic checks on a draft. The model's output is never trusted until these pass."""
from .schemas import DraftSummary


def verify(draft: DraftSummary, findings: dict, regulations: list):
    errors = []
    cands = {c["equipment_id"]: c for c in findings["candidates"]}
    with_issues = {eid for eid, c in cands.items() if c["issues"]}
    ids_by_eq = {eid: {s for i in c["issues"] for s in i["source_ids"]} for eid, c in cands.items()}
    reg_sections = {r["section"] for r in regulations}
    conflict = any(i["type"] == "calibration_conflict" for c in cands.values() for i in c["issues"])

    cause = draft.probable_cause_equipment_id
    if cause is not None and cause not in with_issues:
        errors.append(f"probable_cause_equipment_id {cause} has no supporting maintenance or calibration record")
    if not with_issues and cause is not None:
        errors.append("a cause was named but no candidate instrument has any issue")

    for f in draft.equipment_findings:
        if f.equipment_id not in cands:
            errors.append(f"finding cites {f.equipment_id}, which is not a candidate instrument")
            continue
        if not f.source_ids:
            errors.append(f"finding for {f.equipment_id} cites no record id")
        for s in f.source_ids:
            if s not in ids_by_eq[f.equipment_id]:
                errors.append(f"finding for {f.equipment_id} cites {s}, which is not one of its issue records")

    if cause is not None and cause not in {f.equipment_id for f in draft.equipment_findings}:
        errors.append(f"cause {cause} has no equipment finding backing it")

    for r in draft.regulatory_refs:
        if r.section.replace("§", "").replace("21 CFR", "").strip() not in reg_sections:
            errors.append(f"cites regulation {r.section}, which was not retrieved")
    if not draft.regulatory_refs:
        errors.append("no regulatory reference cited")

    if conflict and not draft.escalation_reason:
        errors.append("calibration records conflict but escalation_reason is empty")
    if conflict and cause is not None:
        errors.append("a cause was assigned although the implicated calibration records conflict")
    if not draft.recommended_actions:
        errors.append("no recommended actions")
    return errors
