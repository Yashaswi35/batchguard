"""Eval regression gate. CI replays recorded Gemini responses and fails the PR if any metric drops."""
import os
from pathlib import Path

import pytest

from evals.run_evals import REGS, run

THRESHOLDS = {
    "task_success_pct": 85.0,
    "grounding_pct": 95.0,
    "citation_validity_pct": 100.0,
    "safe_failure_pct": 100.0,
}


@pytest.fixture(scope="module")
def summary():
    llm = os.environ.get("EVAL_LLM", "replay")
    regs = Path(os.environ.get("EVAL_REGS", REGS))
    s, rows = run(llm, regs=regs)
    return s


@pytest.mark.parametrize("metric,minimum", THRESHOLDS.items())
def test_metric_meets_threshold(summary, metric, minimum):
    assert summary[metric] is not None and summary[metric] >= minimum, \
        f"{metric} = {summary[metric]} is below the release threshold of {minimum}"
