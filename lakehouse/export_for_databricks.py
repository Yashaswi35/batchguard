"""Write Spark-friendly copies of the six source extracts for upload to a Databricks volume.

Spark cannot read pandas' nanosecond parquet timestamps, so datetime columns are written as
'YYYY-MM-DD HH:MM:SS' strings and cast back to timestamps in the silver layer.

Usage: python -m lakehouse.export_for_databricks
"""
import pandas as pd

from pipeline import config as C

FILES = ["indpensim_process.parquet", "indpensim_batches.parquet", "batch_schedule.parquet",
         "cmms_equipment.parquet", "cmms_calibrations.parquet", "cmms_work_orders.parquet"]
OUT = C.DATA_DIR / "databricks_upload"


def main():
    OUT.mkdir(exist_ok=True)
    for f in FILES:
        df = pd.read_parquet(C.DATA_DIR / f)
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
        df.to_parquet(OUT / f, index=False)
        print(f"{f:<28} {len(df):>7,} rows  {(OUT / f).stat().st_size / 1e6:5.1f} MB")
    print(f"\nUpload every file in {OUT} to the Databricks volume /Volumes/workspace/batchguard/raw")


if __name__ == "__main__":
    main()
