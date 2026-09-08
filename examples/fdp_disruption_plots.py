#!/usr/bin/env python3

"""
Plot example traces from the FDP-generated DIII-D disruption dataset.

Draws one disrupted and one non-disrupted shot side by side over a common set of
disruption-relevant parameters, so the shape of the data disruption-py produces
is visible at a glance.

    pixi run plots
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

DEFAULT_DIR = Path("fdp_output")

# (column, label, scale) -- scale converts to the unit in the label.
PANELS = [
    ("ip", "$I_p$ [MA]", 1e-6),
    ("beta_n", r"$\beta_N$", 1.0),
    ("li", "$l_i$", 1.0),
    ("q95", "$q_{95}$", 1.0),
    ("greenwald_fraction", "Greenwald fraction", 1.0),
    ("n_e", r"$n_e$ [$10^{19}$ m$^{-3}$]", 1e-19),
    ("p_rad", "$P_{rad}$ [MW]", 1e-6),
    ("n1rms_normalized", "n=1 RMS (normalized)", 1.0),
    ("time_until_disrupt", "Time until disrupt [s]", 1.0),
]


def _pick(ds, disrupted: bool):
    """Return the shot number of the longest shot in the requested class."""
    tud = ds["time_until_disrupt"].to_numpy()
    shots = ds["shot"].to_numpy()
    best, best_n = None, -1
    for s in np.unique(shots):
        m = shots == s
        is_dis = np.isfinite(tud[m]).any()
        if is_dis != disrupted:
            continue
        if m.sum() > best_n:
            best, best_n = int(s), int(m.sum())
    return best


def main():
    """Render the two-column comparison figure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--disrupted", type=int, default=None)
    parser.add_argument("--non-disrupted", type=int, default=None)
    args = parser.parse_args()

    ds = xr.open_dataset(args.indir / "d3d_disruption_example.nc")
    shot_d = args.disrupted or _pick(ds, True)
    shot_n = args.non_disrupted or _pick(ds, False)
    print(f"disrupted: {shot_d}   non-disrupted: {shot_n}")

    shots = ds["shot"].to_numpy()
    times = ds["time"].to_numpy()

    fig, axes = plt.subplots(
        len(PANELS), 2, figsize=(11, 1.35 * len(PANELS)), sharex="col"
    )
    for col, (shot, title) in enumerate(
        [(shot_d, f"#{shot_d} — disrupted"), (shot_n, f"#{shot_n} — no disruption")]
    ):
        m = shots == shot
        t = times[m]
        for row, (name, label, scale) in enumerate(PANELS):
            ax = axes[row, col]
            y = ds[name].to_numpy()[m] * scale if name in ds else np.full(t.shape, np.nan)
            ax.plot(t, y, lw=1.0, color=f"C{col}")
            if not np.isfinite(y).any():
                # An all-NaN panel is meaningful (time_until_disrupt on a shot
                # that did not disrupt); say so rather than showing empty axes.
                ax.text(
                    0.5, 0.5, "not defined for this shot", transform=ax.transAxes,
                    ha="center", va="center", fontsize=7, color="0.45", style="italic",
                )
            # Label the left column only; the right column shares the quantity.
            if col == 0:
                ax.set_ylabel(label, fontsize=8)
            ax.tick_params(labelsize=7)
            ax.grid(alpha=0.3)
            if row == 0:
                ax.set_title(title, fontsize=11)
            if row == len(PANELS) - 1:
                ax.set_xlabel("Time [s]", fontsize=9)

    fig.suptitle(
        "DIII-D disruption parameters from disruption-py, fetched from the FDP origin",
        fontsize=12,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    out = args.indir / "example_shots.png"
    fig.savefig(out, dpi=150)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
