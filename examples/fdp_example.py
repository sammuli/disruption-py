#!/usr/bin/env python3

"""
Example: read DIII-D data over FDP (Pelican) with disruption-py.

Computes a few disruption-relevant parameters for one DIII-D shot against the
FDP origin instead of an on-site atlas MDSplus server.

This needs NO disruption-py code changes. The origin runs an mdsip relay that
speaks the ordinary MDSplus thin-client protocol, so the stock MDSConnection
works against it unmodified -- the only difference from a normal run is the
connection string:

    fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip

PTDATA pointnames, stored tree nodes and PTDATA2-backed nodes all resolve over
that one connection, because the physics methods pass them as TDI expressions
that the server evaluates. The SQL layer (disruption times, EFIT-tree selection)
is unchanged; this example uses the default d3drdb connection. If you are OFF-SITE and cannot reach d3drdb, use the DummyDatabase
variant shown in the comment below.

Run it inside an FDP-configured environment (so PTDATA_LOC, the Pelican tree
paths, and BEARER_TOKEN are set):

    fdp run python examples/fdp_example.py

(Note: this file is deliberately NOT named ``fdp.py`` — a script named ``fdp.py``
would shadow the installed ``fdp`` package when run directly, breaking
``toksearch_d3d``'s ``from fdp.environment import ...`` with a circular import.)

Prerequisites:
  - the mdsip-fdp package (provides the fdp:// MDSplus transport)
  - a valid FDP bearer token (run `fdp login` first)
"""

import numpy as np

from disruption_py.inout.mds import ProcessMDSConnection

# The FDP origin's mdsip relay speaks the ordinary MDSplus thin-client protocol,
# so the stock MDSConnection works against it unchanged -- only the connection
# string differs from the "atlas" default.
FDP_D3D = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"


# Must be a module-level function, not a lambda: connection_initializer is
# pickled to the worker processes when num_processes > 1.
def fdp_connection():
    """Stock MDSplus connection, pointed at the DIII-D FDP origin."""
    # If you have the `fdp` package installed you can derive this instead of
    # hard-coding it:
    #     from fdp import catalog
    #     origin = catalog["d3d"].schema.origin_server   # root://host:port
    #     url = "fdp://" + origin.split("://", 1)[-1] + "/mdsip"
    # It is not a dependency of this example.
    return ProcessMDSConnection(FDP_D3D)

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
        # --- this is what sends signal retrieval to the FDP origin ---
        connection_initializer=fdp_connection,
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
    print(f"\nShot {shot}: peak |Ip| = {peak_ip_ma:.3f} MA (fetched from the FDP origin)")


if __name__ == "__main__":
    main()
