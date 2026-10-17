"""Run the gold set through the agent and report metrics.

  python -m evals.run_evals --llm rule      deterministic baseline, no model
  python -m evals.run_evals --llm record    live Gemini, saves responses to evals/recordings
  python -m evals.run_evals --llm replay    replays recorded Gemini responses (what CI runs)

Uses the frozen gold snapshot in evals/fixtures by default, so results are reproducible in CI.
"""
import argparse
import json
import tempfile
from pathlib import Path

from agent.audit import AuditLog
from agent.graph import build_graph, investigate
from agent.llm import get_llm
from agent.tools import GoldStore, RegRetriever

from .metrics import aggregate, score_case

HERE = Path(__file__).parent
REPO = HERE.parent
FIXTURE_LAKE = HERE / "fixtures"
REGS = REPO / "regs" / "cfr_21_part_211.json"


def run(llm_kind, lake=FIXTURE_LAKE, regs=REGS, gold=HERE / "gold_set.jsonl"):
    cases = [json.loads(line) for line in Path(gold).read_text().splitlines() if line.strip()]
    llm = get_llm(llm_kind)
    retriever = RegRetriever(regs)
    corpus_sections = set(retriever.by_section)
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditLog(Path(tmp) / "eval_audit.db")
        app = build_graph(GoldStore(lake), retriever, llm, audit)
        rows = []
        for c in cases:
            result = investigate(app, c["batch_id"])
            rows.append(score_case(c, result, audit.trail(result["run_id"]), corpus_sections))
            r = rows[-1]
            print(f"{c['case_id']:<10} {r['status']:<17} cause={str(r['cause']):<8} "
                  f"{'PASS' if r['task_success'] else 'FAIL'}  attempts={r['attempts']}"
                  + (f"  first_errors={r['first_attempt_errors'][:1]}" if r['first_attempt_errors'] else ""))
        audit.con.close()
    summary = aggregate(rows)
    summary["model"] = llm.name
    return summary, rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--llm", default="replay", choices=["rule", "gemini", "record", "replay"])
    p.add_argument("--lake", default=str(FIXTURE_LAKE))
    p.add_argument("--regs", default=str(REGS))
    a = p.parse_args()
    summary, rows = run(a.llm, Path(a.lake), Path(a.regs))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    name = "rule" if a.llm == "rule" else "gemini"
    (out / f"{name}.json").write_text(json.dumps(dict(summary=summary, cases=rows), indent=1, default=str))
    print("\n" + json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
