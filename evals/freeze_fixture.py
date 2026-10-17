"""Copy the gold tables the agent reads into evals/fixtures/gold, so CI can replay evals without raw data.

Run after `python -m pipeline.run`. The snapshot is small (a few hundred KB) and contains no labels.
"""
import shutil

from pipeline import config as C

DEST = C.REPO / "evals" / "fixtures" / "gold"
DEST.mkdir(parents=True, exist_ok=True)
for name in ["batch_summary", "deviation_events", "equipment_context"]:
    shutil.copy(C.LAKE / "gold" / f"{name}.parquet", DEST / f"{name}.parquet")
    print(f"froze {name}: {(DEST / f'{name}.parquet').stat().st_size / 1e3:.0f} KB")
