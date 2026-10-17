"""Read-only tools over the gold data products and the regulation corpus.

Every fact a tool returns carries the record id it came from, so the draft can be verified.
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

import pandas as pd

ISSUE_SEVERITY = {"calibration_conflict": 4, "calibration_failed": 3, "open_corrective_wo": 3,
                  "calibration_overdue": 2, "preventive_maintenance_overdue": 2}


class GoldStore:
    def __init__(self, lake: Path):
        gold = Path(lake) / "gold"
        self.summary = pd.read_parquet(gold / "batch_summary.parquet").set_index("batch_id")
        self.events = pd.read_parquet(gold / "deviation_events.parquet")
        self.context = pd.read_parquet(gold / "equipment_context.parquet")

    def get_batch(self, batch_id: int):
        if batch_id not in self.summary.index:
            return None
        s = self.summary.loc[batch_id]
        return dict(batch_id=int(batch_id), reactor_id=s.reactor_id, control_strategy=s.control_strategy,
                    start_ts=str(s.start_ts), end_ts=str(s.end_ts), deviation_flag=bool(s.deviation_flag),
                    window=[str(s.window_start_ts), str(s.window_end_ts)])

    def get_deviation(self, batch_id: int):
        ev = self.events[(self.events.batch_id == batch_id) & self.events.significant]
        out = []
        for var, g in ev.groupby("variable"):
            out.append(dict(name=var, max_abs_z=float(g.max_abs_z.max()),
                            direction=g.loc[g.max_abs_z.idxmax(), "direction"],
                            first_start=str(g.start_ts.min()), last_end=str(g.end_ts.max()),
                            event_ids=sorted(g.event_id)))
        return sorted(out, key=lambda d: -d["max_abs_z"])

    def get_equipment_findings(self, batch_id: int):
        """Candidate instruments linked to the deviating variables, with issues and record ids.

        Work order notes are returned separately as untrusted text, never mixed with facts.
        """
        rows = self.context[(self.context.batch_id == batch_id) & self.context.linked_to_deviation]
        candidates, notes, allowed = [], [], set()
        for r in rows.itertuples():
            issues = []
            cal_ids = json.loads(r.last_cal_ids)
            if r.last_cal_result == "CONFLICT":
                issues.append(dict(type="calibration_conflict", source_ids=cal_ids,
                                   detail=f"Calibration on {str(r.last_cal_date)[:10]} has conflicting PASS and "
                                          f"FAIL records: {r.last_cal_note}"))
            elif r.last_cal_result == "FAIL":
                issues.append(dict(type="calibration_failed", source_ids=cal_ids,
                                   detail=f"Last calibration {str(r.last_cal_date)[:10]} FAILED: {r.last_cal_note}"))
            if r.cal_overdue:
                issues.append(dict(type="calibration_overdue", source_ids=cal_ids,
                                   detail=f"Calibration due {str(r.cal_due_date)[:10]}, "
                                          f"{r.cal_days_overdue} days overdue at window start"))
            for w in json.loads(r.open_corrective_wos):
                issues.append(dict(type="open_corrective_wo", source_ids=[w["wo_id"]],
                                   detail=f"Corrective work order open since {w['opened_ts'][:10]}: {w['description']}"))
            for w in json.loads(r.overdue_pm_wos):
                issues.append(dict(type="preventive_maintenance_overdue", source_ids=[w["wo_id"]],
                                   detail=f"Preventive maintenance due {w['due_ts'][:10]} not completed: "
                                          f"{w['description']}"))
            for w in json.loads(r.recent_closed_wos):
                notes.append(dict(equipment_id=r.equipment_id, wo_id=w["wo_id"], text=w["notes"]))
            for i in issues:
                allowed.update(i["source_ids"])
            candidates.append(dict(equipment_id=r.equipment_id, description=r.description,
                                   linked_variables=json.loads(r.linked_variables),
                                   last_calibration=str(r.last_cal_date)[:10] if r.last_cal_ids != "[]" else None,
                                   issues=issues))
        candidates.sort(key=lambda c: -max([ISSUE_SEVERITY[i["type"]] for i in c["issues"]] or [0]))
        return dict(candidates=candidates, untrusted_notes=notes, allowed_source_ids=sorted(allowed))


class RegRetriever:
    """Small BM25 retriever over 21 CFR 211 sections. 211.192 is always included for deviations."""

    PINNED = ["211.192"]

    def __init__(self, corpus_path: Path, k1=1.5, b=0.75):
        self.docs = json.loads(Path(corpus_path).read_text())
        self.by_section = {d["section"]: d for d in self.docs}
        self.tokens = [self._tok(d["heading"] + " " + d["text"]) for d in self.docs]
        self.avgdl = sum(map(len, self.tokens)) / len(self.tokens)
        df = Counter(t for toks in self.tokens for t in set(toks))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(toks) for toks in self.tokens]
        self.k1, self.b = k1, b

    @staticmethod
    def _tok(s):
        return re.findall(r"[a-z]+", s.lower())

    def search(self, query: str, k=3):
        q = self._tok(query)
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.tokens[i])
            s = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) /
                    (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl)) for t in q if t in tf)
            scores.append((s, i))
        top = [self.docs[i]["section"] for s, i in sorted(scores, reverse=True)[:k] if s > 0]
        sections = top + [p for p in self.PINNED if p in self.by_section and p not in top]
        return [dict(section=s, heading=self.by_section[s]["heading"],
                     text=self.by_section[s]["text"][:1500]) for s in sections]


def build_reg_query(deviation, findings):
    words = ["equipment", "deviation", "investigation"]
    for c in findings["candidates"]:
        words.append(c["description"].split(",")[0])
        for i in c["issues"]:
            words += {"calibration_conflict": ["calibration", "records"], "calibration_failed": ["calibration"],
                      "calibration_overdue": ["calibration", "calibrated"],
                      "open_corrective_wo": ["maintenance", "repair"],
                      "preventive_maintenance_overdue": ["maintenance", "schedule"]}[i["type"]]
    return " ".join(words)
