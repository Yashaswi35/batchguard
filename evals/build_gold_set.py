"""Build evals/gold_set.jsonl: 25 cases with expected outcomes. Run once, commit the output."""
import json
from pathlib import Path

HERE = Path(__file__).parent
scenarios = {s["batch_id"]: s for s in json.loads((HERE / "planted_scenarios.json").read_text())}

cases = []
for b in range(91, 101):
    s = scenarios[b]
    cases.append(dict(case_id=f"fault-{b}", category="faulted", batch_id=b, kind=s["kind"],
                      expected_status="ESCALATED" if s["expect_escalation"] else "PENDING_APPROVAL",
                      expected_cause=s["expected_cause_equipment"]))

# Normal batches across all control strategies, including the highest-z normal batches and two traps:
# 45 has an overdue pH calibration and 89 overlaps an overdue pump PM, but neither has a deviation.
for b in [5, 24, 33, 39, 45, 51, 60, 68, 77, 89]:
    cases.append(dict(case_id=f"normal-{b}", category="normal", batch_id=b,
                      kind=scenarios[b]["kind"] if b in scenarios else "normal",
                      expected_status="NO_DEVIATION", expected_cause=None))

for i, bad in enumerate([0, 999, -1, "92; DROP TABLE batches", "ninety-two"]):
    cases.append(dict(case_id=f"input-{i}", category="adversarial_input", batch_id=bad, kind="invalid_input",
                      expected_status="REJECTED_INPUT", expected_cause=None))

(HERE / "gold_set.jsonl").write_text("\n".join(json.dumps(c) for c in cases) + "\n")
print(f"wrote {len(cases)} cases to evals/gold_set.jsonl")
