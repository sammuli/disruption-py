#!/usr/bin/env python3

"""
Build an example DIII-D disruption dataset with disruption-py, over FDP.

Every signal is fetched through the Fusion Data Platform -- no on-site MDSplus
or PTData server -- by injecting the FDP backend:

    connection_initializer=ProcessFDPConnection

MDSplus reads go over the ``fdp://`` mdsip transport (the origin evaluates TDI
server-side, so PTDATA2-backed nodes like ``\\fs04`` and ``\\top.nb:pinj``
resolve); PTDATA pointnames go over the Pelican JSON index via PtDataSignal.

The shot list mixes disrupted and non-disrupted plasma shots so the resulting
table shows both classes and a populated ``time_until_disrupt`` column.

Run it inside an FDP-configured environment:

    pixi run dataset

Output: a NetCDF file (full fidelity, one group per shot) and a flat CSV.
"""

import argparse
import time
from pathlib import Path

from disruption_py.inout.fdp import ProcessFDPConnection
from disruption_py.machine.tokamak import Tokamak
from disruption_py.settings import LogSettings, RetrievalSettings
from disruption_py.workflow import get_shots_data

# 10 disrupted + 10 non-disrupted DIII-D plasma shots, sampled across the
# 2023 and 2024 campaigns (source: d3drdb `shots`/`shots_type`/`disruptions`).
DISRUPTED = [194000, 194128, 194292, 194464, 194649, 194968, 195516, 195768, 195985, 196347]
NON_DISRUPTED = [194005, 194869, 195243, 195805, 196273, 198244, 198643, 199062, 199359, 199687]
SHOTS = sorted(DISRUPTED + NON_DISRUPTED)


def main():
    """Run disruption-py over the example shot list and write the dataset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("/fusion/projects/dt/sammuli/fdp_dev/repos/feder-disruption-example"),
        help="directory for the .nc / .csv output",
    )
    parser.add_argument(
        "--shots", type=int, nargs="*", default=SHOTS, help="override the shot list"
    )
    parser.add_argument("--num-processes", type=int, default=8)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    nc_path = args.outdir / "d3d_disruption_example.nc"
    csv_path = args.outdir / "d3d_disruption_example.csv"

    # Defaults run every D3D physics method and use the SQL-backed disruption
    # timebase + EFIT-tree selection, which is what the reference dataset uses.
    retrieval_settings = RetrievalSettings()

    started = time.time()
    result = get_shots_data(
        tokamak=Tokamak.D3D,
        shotlist_setting=args.shots,
        retrieval_settings=retrieval_settings,
        # --- everything below the physics methods is fetched over FDP ---
        connection_initializer=ProcessFDPConnection,
        output_setting=[str(nc_path), str(csv_path)],
        num_processes=args.num_processes,
        log_settings=LogSettings(console_level="INFO"),
    )
    elapsed = time.time() - started

    print(f"\nWrote {nc_path}")
    print(f"Wrote {csv_path}")
    print(f"{len(args.shots)} shots in {elapsed/60:.1f} min")
    return result


if __name__ == "__main__":
    main()
