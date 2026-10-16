"""Extract IndPenSim process columns (drop Raman), fix header misalignment, save Parquet.

The raw CSV header has 2239 names but data rows have 2237 values (a header label
was split by a comma), so we skip the header and assign names by position.
"""
import sys
from pathlib import Path
import pandas as pd

SRC = Path(sys.argv[1]).expanduser()
STATS = Path(sys.argv[2]).expanduser()
OUT_DIR = Path(sys.argv[3]).expanduser()

NAMES = [
    "time_h", "aeration_rate_l_h", "agitator_rpm", "sugar_feed_l_h", "acid_flow_l_h",
    "base_flow_l_h", "cooling_water_l_h", "heating_water_l_h", "water_injection_l_h",
    "air_head_pressure_bar", "dumped_broth_l_h", "substrate_g_l", "dissolved_o2_mg_l",
    "penicillin_g_l", "vessel_volume_l", "vessel_weight_kg", "ph", "temperature_k",
    "generated_heat_kj", "co2_offgas_pct", "paa_flow_l_h", "paa_offline_g_l",
    "oil_flow_l_h", "nh3_offline_g_l", "our_g_min", "o2_offgas_pct",
    "penicillin_offline_g_l", "biomass_offline_g_l", "cer_g_h", "ammonia_shots_kg",
    "viscosity_offline_cp",
    # positions 31-36, verified against raw rows
    "fault_ref", "control_ref", "raman_flag", "batch_ref", "batch_id", "fault_flag",
]

chunks = []
reader = pd.read_csv(SRC, header=None, skiprows=1, usecols=range(len(NAMES)),
                     names=NAMES, chunksize=20000, low_memory=False)
for i, chunk in enumerate(reader):
    chunks.append(chunk)
    print(f"chunk {i+1}: {sum(len(c) for c in chunks):,} rows", end="\r")
df = pd.concat(chunks, ignore_index=True)
df["batch_id"] = df["batch_id"].astype(int)

stats = pd.read_csv(STATS)
stats.columns = ["batch_id", "pen_harvested_during_kg", "pen_harvested_end_kg",
                 "pen_yield_total_kg", "is_faulted"]
def strategy(b):
    return ("recipe" if b <= 30 else "operator" if b <= 60
            else "apc_raman" if b <= 90 else "faulted")
stats["control_strategy"] = stats["batch_id"].apply(strategy)

assert df["batch_id"].nunique() == 100, f"expected 100 batches, got {df.batch_id.nunique()}"
assert (df["batch_id"] == df["batch_ref"]).all(), "batch_id and batch_ref disagree"
assert set(df["batch_id"]) == set(stats["batch_id"]), "batch ids differ from stats file"

OUT_DIR.mkdir(parents=True, exist_ok=True)
df.to_parquet(OUT_DIR / "indpensim_process.parquet", index=False)
stats.to_parquet(OUT_DIR / "indpensim_batches.parquet", index=False)

print(f"\nrows={len(df):,} batches={df.batch_id.nunique()} "
      f"size={(OUT_DIR/'indpensim_process.parquet').stat().st_size/1e6:.1f} MB")
s = df.groupby("batch_id").agg(rows=("time_h", "size"), hours=("time_h", "max"),
                               fault_flag_max=("fault_flag", "max"),
                               final_pen=("penicillin_g_l", "last"))
s = s.join(stats.set_index("batch_id")[["is_faulted", "control_strategy"]])
print(s.iloc[[0, 1, 29, 30, 59, 60, 89] + list(range(90, 100))].to_string())
print("\nfaulted batches (stats file):", stats.loc[stats.is_faulted == 1, "batch_id"].tolist())
print("rows per batch: min", s.rows.min(), "max", s.rows.max())
