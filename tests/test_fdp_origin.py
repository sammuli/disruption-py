"""Opt-in smoke test: the FDP origin serves everything MDSConnection needs.

This is the evidence behind the claim in docs/usage/fdp.md that no custom data
backend is required -- the origin's mdsip relay speaks the ordinary MDSplus
thin-client protocol and evaluates TDI server-side, so stored tree nodes,
PTDATA pointnames and PTDATA2-backed nodes all resolve over one connection.

Runs only when BEARER_TOKEN is set. Inside an FDP env:
    fdp run python -m pytest tests/test_fdp_origin.py -v
"""

import os

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BEARER_TOKEN"),
    reason="FDP origin test requires BEARER_TOKEN (run via `fdp run`).",
)

SHOT = 161228  # known good DIII-D plasma shot


FDP_D3D = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"


@pytest.fixture(scope="module")
def conn():
    """A stock MDSplus thin-client connection to the DIII-D FDP origin."""
    MDSplus = pytest.importorskip("MDSplus")
    return MDSplus.Connection(FDP_D3D)


def _finite(result):
    arr = np.asarray(result.data() if hasattr(result, "data") else result)
    assert arr.size > 0
    assert np.isfinite(arr).any()
    return arr


def test_stored_tree_nodes_resolve(conn):
    """EFIT nodes -- the ordinary stored-record case."""
    conn.openTree("efit01", SHOT)
    for node in (r"\efit_a_eqdsk:li", r"\efit_a_eqdsk:betan"):
        _finite(conn.get(node))


def test_ptdata_tdi_is_evaluated_server_side(conn):
    """The physics methods pass ptdata('name', shot) as a TDI string."""
    signal = _finite(conn.get(f"ptdata('ip',{SHOT})"))
    times = _finite(conn.get(f"dim_of(ptdata('ip',{SHOT}))"))
    assert signal.shape == times.shape
    assert np.nanmax(np.abs(signal)) > 1e5  # Ip in amps, ~MA scale


def test_ptdata2_backed_node_resolves(conn):
    """\\fs04's stored record calls PTDATA2 -- it resolves like any other node."""
    conn.openTree("d3d", SHOT)
    _finite(conn.get(r"\fs04"))
