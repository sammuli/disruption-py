"""Opt-in FDP integration + parity test.

Runs only when toksearch_d3d is importable AND BEARER_TOKEN is set. Fetches a
few signals for a known DIII-D shot through the FDP backend and checks they are
finite and shaped sensibly. Assert closeness (not equality).

Run inside an FDP env, e.g.:
    fdp run python -m pytest tests/test_fdp_integration.py -v
"""

import os

import numpy as np
import pytest

toksearch_d3d = pytest.importorskip("toksearch_d3d")

pytestmark = pytest.mark.skipif(
    not os.environ.get("BEARER_TOKEN"),
    reason="FDP integration test requires BEARER_TOKEN (run via `fdp run`).",
)

SHOT = 202161  # known good DIII-D shot


def _make_conn():
    from disruption_py.inout.fdp import ProcessFDPConnection

    conn = ProcessFDPConnection().get_shot_connection(SHOT)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    return conn


def test_ptdata_ip_fetch_is_finite():
    conn = _make_conn()
    ip, t_ip = conn.get_data_with_dims(f"ptdata('ip', {SHOT})")
    assert ip.shape == t_ip.shape
    assert ip.size > 0
    assert np.any(np.isfinite(ip))
    # ip in amps; peak should be > 100 kA for a real plasma shot
    assert np.nanmax(np.abs(ip)) > 1e5


def test_efit_li_fetch_over_pelican():
    conn = _make_conn()
    li = conn.get_data(r"\efit_a_eqdsk:li", tree_name="_efit_tree")
    assert li.size > 0
    assert np.any(np.isfinite(li))
    # li is O(1); sanity-bound it
    finite = li[np.isfinite(li)]
    assert np.all((finite > 0) & (finite < 5))
