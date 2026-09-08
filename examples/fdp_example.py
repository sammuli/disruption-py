#!/usr/bin/env python3

"""
Example: read DIII-D data over FDP (Pelican) with disruption-py.

Computes a few disruption-relevant parameters for one DIII-D shot through the
FDP/Pelican backend instead of a direct atlas MDSplus connection:

  - ip                  -> PtDataSignal  (PTDATA over Pelican)
  - beta_n, li, kappa   -> MdsSignal     (EFIT trees over Pelican)

The single line that routes signal retrieval through FDP is:

    connection_initializer=ProcessFDPConnection

Everything else is ordinary disruption-py. The SQL layer (disruption times,
EFIT-tree selection) is unchanged; this example uses the default d3drdb
connection. If you are OFF-SITE and cannot reach d3drdb, use the DummyDatabase
variant shown in the comment below.

Run it inside an FDP-configured environment (so PTDATA_LOC, the Pelican tree
paths, and BEARER_TOKEN are set):

    fdp run python examples/fdp_example.py

(Note: this file is deliberately NOT named ``fdp.py`` — a script named ``fdp.py``
would shadow the installed ``fdp`` package when run directly, breaking
``toksearch_d3d``'s ``from fdp.environment import ...`` with a circular import.)

Prerequisites:
  - toksearch and toksearch_d3d installed in that environment
  - a valid FDP bearer token (run `fdp login` first)
"""

import numpy as np

from disruption_py.inout.fdp import ProcessFDPConnection
from disruption_py.machine.tokamak import Tokamak
from disruption_py.settings import RetrievalSettings
from disruption_py.workflow import get_shots_data


def main():
    """Fetch ip + a few EFIT parameters for one DIII-D shot over FDP."""

    shot = 161228  # a DIII-D plasma shot; swap for one you care about

    retrieval_settings = RetrievalSettings(
        # Pick the parameters to compute; disruption-py runs the physics methods
        # that produce them. "ip" exercises the PTDATA path; the EFIT columns
        # exercise the MDSplus-over-Pelican path.
        run_columns=["ip", "beta_n", "li", "kappa"],
    )

    result = get_shots_data(
        tokamak=Tokamak.D3D,
        shotlist_setting=[shot],
        retrieval_settings=retrieval_settings,
        # --- this is what sends signal retrieval through FDP/Pelican ---
        connection_initializer=ProcessFDPConnection,
        # Uses the default d3drdb SQL connection (disruption times + EFIT-tree
        # selection). OFF-SITE (no d3drdb) alternative — pins EFIT to "efit01"
        # and needs no SQL:
        #
        #   from disruption_py.inout.sql import DummyDatabase
        #   ... database_initializer=DummyDatabase.initializer,
        #       retrieval_settings=RetrievalSettings(
        #           run_columns=["ip", "beta_n", "li", "kappa"],
        #           efit_nickname_setting="default",
        #           time_setting=np.arange(0.1, 3.0, 0.02),  # seconds
        #       ),
        output_setting="dataset",
        num_processes=1,
    )

    print(result)
    peak_ip_ma = float(np.nanmax(np.abs(result["ip"]))) / 1e6
    print(f"\nShot {shot}: peak |Ip| = {peak_ip_ma:.3f} MA (fetched over FDP)")


if __name__ == "__main__":
    main()
