"""LLM backends behind one interface: generate(system, user) -> JSON text.

- GeminiLLM:    live calls to the Gemini API (GOOGLE_API_KEY), structured JSON output
- RuleBasedLLM: deterministic baseline with no model, used to test the graph and as a comparison
- RecordingLLM: wraps a live model and saves every response to evals/recordings
- ReplayLLM:    serves recorded responses, so CI runs real model outputs with no API key or randomness
"""
import hashlib
import json
import os
from pathlib import Path

from .schemas import DraftSummary

REPO = Path(__file__).resolve().parents[1]
RECORDINGS = REPO / "evals" / "recordings"


def _key(model, system, user):
    return hashlib.sha256(f"{model}\n{system}\n{user}".encode()).hexdigest()[:16]


class GeminiLLM:
    def __init__(self, model=None):
        from dotenv import load_dotenv
        from google import genai
        load_dotenv(REPO / ".env")
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Set GOOGLE_API_KEY in .env")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self.name = self.model
        self.client = genai.Client(api_key=key)

    def generate(self, system, user, max_tries=6):
        import time

        from google.genai import errors, types
        for attempt in range(max_tries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=user,
                    config=types.GenerateContentConfig(system_instruction=system, temperature=0,
                                                       response_mime_type="application/json",
                                                       response_schema=DraftSummary))
                return resp.text
            except errors.APIError as e:
                # free tier rate limits (429) and transient server errors: back off and retry
                if getattr(e, "code", None) in (429, 500, 503) and attempt < max_tries - 1:
                    time.sleep(min(60, 5 * 2 ** attempt))
                    continue
                raise


class RecordingLLM:
    def __init__(self, inner, directory=RECORDINGS):
        self.inner, self.dir, self.name = inner, Path(directory), inner.name
        self.dir.mkdir(parents=True, exist_ok=True)

    def generate(self, system, user):
        out = self.inner.generate(system, user)
        (self.dir / f"{_key(self.name, system, user)}.json").write_text(
            json.dumps(dict(model=self.name, response=out), indent=1))
        return out


class ReplayLLM:
    def __init__(self, model=None, directory=RECORDINGS):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self.name = self.model
        self.dir = Path(directory)

    def generate(self, system, user):
        f = self.dir / f"{_key(self.model, system, user)}.json"
        if not f.exists():
            raise FileNotFoundError(f"No recorded response for this prompt ({f.name}). The prompt or context "
                                    f"changed, re-record with: python -m evals.run_evals --llm gemini --record")
        return json.loads(f.read_text())["response"]


class RuleBasedLLM:
    """Deterministic baseline: picks the most severe issue and cites matching sections. No language model."""

    name = "rule-based-baseline"

    def generate(self, system, user):
        ctx = json.loads(user.split("CONTEXT:\n", 1)[1].split("\n\nWrite the", 1)[0])
        regs = {r["section"] for r in ctx["regulations"]}
        with_issues = [c for c in ctx["candidates"] if c["issues"]]
        conflict = any(i["type"] == "calibration_conflict" for c in with_issues for i in c["issues"])
        findings = [dict(equipment_id=c["equipment_id"], issue=i["detail"][:160], source_ids=i["source_ids"])
                    for c in with_issues for i in c["issues"]]
        refs = []
        cal = any("calibration" in f["issue"].lower() for f in findings)
        for sec, why, cond in [("211.68", "Instruments must be calibrated and checked on a written schedule.", cal),
                               ("211.67", "Equipment must be maintained at appropriate intervals.", not cal and findings),
                               ("211.192", "Unexplained discrepancies must be investigated and documented.", True)]:
            if cond and sec in regs:
                refs.append(dict(section=sec, relevance=why))
        if conflict:
            top = None
            cause = "Calibration records for the implicated instrument conflict, so no cause can be assigned."
        elif with_issues:
            top = with_issues[0]["equipment_id"]
            cause = f"{top} had an open maintenance or calibration issue during the deviation window."
        else:
            top = None
            cause = "Maintenance and calibration records show no equipment cause for this deviation."
        draft = DraftSummary(
            probable_cause_equipment_id=top, probable_cause=cause, equipment_findings=findings,
            regulatory_refs=refs,
            recommended_actions=(["Verify the implicated instrument and review its records."] if top else
                                 ["Investigate process parameters and raw material lots."])
            + ["Assess impact on other batches run on the same equipment."],
            confidence="low" if conflict else ("high" if top else "medium"),
            escalation_reason="Conflicting calibration records require QA review." if conflict else None)
        return draft.model_dump_json()


def get_llm(kind):
    if kind == "gemini":
        return GeminiLLM()
    if kind == "record":
        return RecordingLLM(GeminiLLM())
    if kind == "replay":
        return ReplayLLM()
    if kind == "rule":
        return RuleBasedLLM()
    raise ValueError(f"unknown llm {kind}")
