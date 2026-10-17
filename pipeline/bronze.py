"""Bronze: land source extracts unchanged, plus lineage columns."""
import pandas as pd

SOURCES = {
    "process": "indpensim_process.parquet",
    "batches": "indpensim_batches.parquet",
    "schedule": "batch_schedule.parquet",
    "equipment": "cmms_equipment.parquet",
    "calibrations": "cmms_calibrations.parquet",
    "work_orders": "cmms_work_orders.parquet",
}


def run(data_dir, lake):
    out = lake / "bronze"
    out.mkdir(parents=True, exist_ok=True)
    ingest_ts = pd.Timestamp.now(tz="UTC")
    counts = {}
    for name, fname in SOURCES.items():
        df = pd.read_parquet(data_dir / fname)
        df["_ingest_ts"] = ingest_ts
        df["_source_file"] = fname
        df.to_parquet(out / f"{name}.parquet", index=False)
        counts[name] = len(df)
    return counts
