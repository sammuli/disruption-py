"""Unit tests for the FDP data connection (signal classes are mocked)."""

import numpy as np
import pytest

import disruption_py.inout.fdp as fdp_mod
from disruption_py.core.physics_method.errors import FetchDataError, NanDataError
from disruption_py.inout.fdp import FDPDataConnection, ProcessFDPConnection
from disruption_py.inout.nickname import TreeNicknameMixin


def test_factory_creates_per_shot_connection():
    proc = ProcessFDPConnection()
    conn = proc.get_shot_connection(shot_id=123456)
    assert isinstance(conn, FDPDataConnection)
    assert conn.shot_id == 123456


def test_connection_is_a_nickname_mixin():
    conn = FDPDataConnection(123456)
    assert isinstance(conn, TreeNicknameMixin)


def test_cleanup_and_reconnect_are_noops():
    conn = FDPDataConnection(123456)
    assert conn.cleanup() is None
    assert conn.reconnect() is None


class _FakePtDataSignal:
    """Records constructor args; returns a fixed fetch payload."""
    last_pointname = None
    last_shot = None

    def __init__(self, pointname, **kwargs):
        _FakePtDataSignal.last_pointname = pointname
        self.kwargs = kwargs

    def fetch(self, shot):
        _FakePtDataSignal.last_shot = shot
        return {
            "data": np.array([1.0, 2.0, 3.0]),
            "times": np.array([0.0, 0.5, 1.0]),
            "units": {"data": "A", "times": "ms"},
        }


def test_ptdata_get_data(monkeypatch):
    monkeypatch.setattr(fdp_mod, "PtDataSignal", _FakePtDataSignal)
    conn = FDPDataConnection(202161)
    data = conn.get_data("ptdata('ip', 202161)")
    assert _FakePtDataSignal.last_pointname == "ip"
    assert _FakePtDataSignal.last_shot == 202161
    np.testing.assert_array_equal(data, [1.0, 2.0, 3.0])


def test_ptdata_double_quotes_and_case(monkeypatch):
    monkeypatch.setattr(fdp_mod, "PtDataSignal", _FakePtDataSignal)
    conn = FDPDataConnection(202161)
    conn.get_data('PTDATA("VLOOPB", 202161)')
    assert _FakePtDataSignal.last_pointname == "VLOOPB"


def test_ptdata_get_data_with_dims(monkeypatch):
    monkeypatch.setattr(fdp_mod, "PtDataSignal", _FakePtDataSignal)
    conn = FDPDataConnection(202161)
    data, times = conn.get_data_with_dims("ptdata('ip', 202161)")
    np.testing.assert_array_equal(data, [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(times, [0.0, 0.5, 1.0])


def test_ptdata_get_dims(monkeypatch):
    monkeypatch.setattr(fdp_mod, "PtDataSignal", _FakePtDataSignal)
    conn = FDPDataConnection(202161)
    (times,) = conn.get_dims("ptdata('ip', 202161)")
    np.testing.assert_array_equal(times, [0.0, 0.5, 1.0])


def test_required_nan_raises(monkeypatch):
    class _NanSignal(_FakePtDataSignal):
        def fetch(self, shot):
            return {"data": np.array([np.nan, np.nan]), "times": np.array([0.0, 0.5])}
    monkeypatch.setattr(fdp_mod, "PtDataSignal", _NanSignal)
    conn = FDPDataConnection(202161)
    with pytest.raises(NanDataError):
        conn.get_data("ptdata('ip', 202161)", required=True)


def test_guarded_imports_are_independent():
    """A present toksearch must not be clobbered when toksearch_d3d is absent.

    Loads a throwaway copy of the module (does NOT reload the shared fdp_mod,
    which would swap class identities and pollute other tests).
    """
    import importlib.util
    import sys
    import types

    fake = types.ModuleType("toksearch")
    fake.MdsSignal = object
    saved = sys.modules.get("toksearch")
    sys.modules["toksearch"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "disruption_py._fdp_import_probe", fdp_mod.__file__
        )
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        assert probe.MdsSignal is not None  # not clobbered by td3d absence
        assert probe.PtDataSignal is None  # toksearch_d3d genuinely absent
    finally:
        if saved is None:
            sys.modules.pop("toksearch", None)
        else:
            sys.modules["toksearch"] = saved


def test_ptdata_fetch_error_is_wrapped(monkeypatch):
    class _BoomPt:
        def __init__(self, *a, **k):
            pass

        def fetch(self, shot):
            raise RuntimeError("boom")

    monkeypatch.setattr(fdp_mod, "PtDataSignal", _BoomPt)
    conn = FDPDataConnection(202161)
    with pytest.raises(FetchDataError, match="ptdata"):
        conn.get_data("ptdata('ip', 202161)")


def test_mds_fetch_error_is_wrapped(monkeypatch):
    class _BoomMds:
        def __init__(self, *a, **k):
            pass

        def fetch(self, shot):
            raise RuntimeError("boom")

    monkeypatch.setattr(fdp_mod, "MdsSignal", _BoomMds)
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    with pytest.raises(FetchDataError, match="mds"):
        conn.get_data(r"\efit_a_eqdsk:li", tree_name="_efit_tree")


class _FakeMdsSignal:
    """Records constructor args; returns data + one array per requested dim name."""
    last_args = None

    def __init__(self, expression, treename, location=None, dims=("times",), **kwargs):
        _FakeMdsSignal.last_args = {
            "expression": expression,
            "treename": treename,
            "location": location,
            "dims": dims,
        }
        self._dims = dims

    def fetch(self, shot):
        result = {"data": np.array([10.0, 20.0])}
        for i, name in enumerate(self._dims):
            result[name] = np.array([float(i), float(i) + 0.5])
        return result


def test_mds_tree_node_routes_and_resolves_nickname(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    data, times = conn.get_data_with_dims(r"\efit_a_eqdsk:li", tree_name="_efit_tree")
    assert _FakeMdsSignal.last_args["expression"] == r"\efit_a_eqdsk:li"
    assert _FakeMdsSignal.last_args["treename"] == "efit01"
    assert _FakeMdsSignal.last_args["location"] is None
    np.testing.assert_array_equal(data, [10.0, 20.0])
    np.testing.assert_array_equal(times, [0.0, 0.5])


def test_mds_group_is_alias_for_tree_name(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161)
    conn.get_data(r"\fs04", group="d3d")
    assert _FakeMdsSignal.last_args["treename"] == "d3d"


def test_mds_get_dims_only(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161)
    (times,) = conn.get_dims(r"\top.raw:chan", tree_name="bolom")
    np.testing.assert_array_equal(times, [0.0, 0.5])


def test_higher_dim_nums_sizes_dims_and_returns_requested(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    data, dim2 = conn.get_data_with_dims(
        r"\top.results.geqdsk:psirz", tree_name="_efit_tree", dim_nums=[2]
    )
    assert _FakeMdsSignal.last_args["dims"] == ("dim0", "dim1", "dim2")
    np.testing.assert_array_equal(dim2, [2.0, 2.5])


def test_ptdata2_backed_node_raises_not_hangs(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    # Simulate a node enumerated as PTDATA2-backed (deferred work).
    monkeypatch.setattr(fdp_mod, "_PTDATA2_BACKED_NODES", {r"\some_ptdata2_node"})
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    with pytest.raises(FetchDataError, match="PTDATA2"):
        conn.get_data(r"\some_ptdata2_node", tree_name="_efit_tree")


def test_get_process_connection_selects_fdp(monkeypatch):
    import disruption_py.workflow as wf
    from disruption_py.machine.tokamak import Tokamak

    class _Cfg:
        # Realistic production case: user's [inout.fdp] merges ON TOP of the
        # shipped [inout.mds] (Dynaconf merge_enabled), so BOTH keys are present.
        # This pins the "fdp checked first" property — a branch reorder must fail.
        inout = {"mds": {}, "fdp": {}}

    sentinel = object()
    monkeypatch.setattr(wf, "resolve_tokamak_from_environment", lambda t: Tokamak.D3D)
    monkeypatch.setattr(wf, "config", lambda tok: _Cfg())
    monkeypatch.setattr(
        wf.ProcessFDPConnection,
        "from_config",
        classmethod(lambda cls, tokamak: sentinel),
    )
    assert wf.get_process_connection(Tokamak.D3D) is sentinel


def test_get_process_connection_defaults_to_mds_when_no_fdp(monkeypatch):
    import disruption_py.workflow as wf
    from disruption_py.machine.tokamak import Tokamak

    class _Cfg:
        inout = {"mds": {}}  # no fdp -> should pick mds, not fdp

    mds_sentinel = object()
    fdp_sentinel = object()
    monkeypatch.setattr(wf, "resolve_tokamak_from_environment", lambda t: Tokamak.D3D)
    monkeypatch.setattr(wf, "config", lambda tok: _Cfg())
    monkeypatch.setattr(
        wf.ProcessMDSConnection,
        "from_config",
        classmethod(lambda cls, tokamak: mds_sentinel),
    )
    monkeypatch.setattr(
        wf.ProcessFDPConnection,
        "from_config",
        classmethod(lambda cls, tokamak: fdp_sentinel),
    )
    result = wf.get_process_connection(Tokamak.D3D)
    assert result is mds_sentinel
    assert result is not fdp_sentinel
