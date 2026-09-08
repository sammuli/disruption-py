"""Unit tests for the FDP data connection (signal classes are mocked)."""

import numpy as np
import pytest

import disruption_py.inout.fdp as fdp_mod
from disruption_py.core.physics_method.errors import FetchDataError, NanDataError
from disruption_py.inout.fdp import FDPDataConnection, ProcessFDPConnection
from disruption_py.inout.mds import mdsExceptions
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
    saved = {name: sys.modules.get(name) for name in ("toksearch", "toksearch_d3d")}
    sys.modules["toksearch"] = fake
    # None in sys.modules makes `import toksearch_d3d` raise ModuleNotFoundError,
    # so the test simulates an absent toksearch_d3d even in an env (like the pixi
    # one) where it is genuinely installed.
    sys.modules["toksearch_d3d"] = None
    try:
        spec = importlib.util.spec_from_file_location(
            "disruption_py._fdp_import_probe", fdp_mod.__file__
        )
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        assert probe.MdsSignal is not None  # not clobbered by td3d absence
        assert probe.PtDataSignal is None  # toksearch_d3d simulated absent
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


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


def test_denylisted_node_raises_not_hangs(monkeypatch):
    """A node on the Pelican-path deny-list fails loudly rather than hanging."""
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    monkeypatch.setattr(fdp_mod, "_PTDATA2_BACKED_NODES", {r"\some_unresolvable_node"})
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    with pytest.raises(FetchDataError, match="not resolvable"):
        conn.get_data(r"\some_unresolvable_node", tree_name="_efit_tree")


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


def test_fdp_fetch_error_is_dual_typed():
    from disruption_py.inout.fdp import FdpFetchError

    err = FdpFetchError("boom")
    assert isinstance(err, FetchDataError)
    assert isinstance(err, mdsExceptions.MdsException)
    assert str(err) == "boom"


def test_ptdata_fetch_error_caught_as_mdsexception(monkeypatch):
    """Mirrors the physics-method fallback pattern: except mdsExceptions.MdsException."""

    class _BoomPt:
        def __init__(self, *a, **k):
            pass

        def fetch(self, shot):
            raise RuntimeError("boom")

    monkeypatch.setattr(fdp_mod, "PtDataSignal", _BoomPt)
    conn = FDPDataConnection(202161)
    caught = False
    try:
        conn.get_data("ptdata('ipsip', 202161)")
    except mdsExceptions.MdsException:
        caught = True
    assert caught


def test_mds_fetch_error_caught_as_mdsexception(monkeypatch):
    class _BoomMds:
        def __init__(self, *a, **k):
            pass

        def fetch(self, shot):
            raise RuntimeError("boom")

    monkeypatch.setattr(fdp_mod, "MdsSignal", _BoomMds)
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    with pytest.raises(mdsExceptions.MdsException):
        conn.get_data(r"\efit_a_eqdsk:li", tree_name="_efit_tree")


def test_ptdata2_guard_is_also_mdsexception(monkeypatch):
    monkeypatch.setattr(fdp_mod, "_PTDATA2_BACKED_NODES", {r"\some_ptdata2_node"})
    conn = FDPDataConnection(202161)
    with pytest.raises(mdsExceptions.MdsException):
        conn.get_data(r"\some_ptdata2_node", tree_name="efit01")


# --- mds_location: fdp:// mdsip transport vs Pelican tree files ---------------


def test_derive_fdp_mds_location_builds_mdsip_url_from_origin(monkeypatch):
    """The catalog's root:// origin becomes the fdp:// mdsip URL, host+port kept."""

    class _Schema:
        origin_server = "root://fdp-d3d-origin.nationalresearchplatform.org:8443"

    class _Handle:
        schema = _Schema()

    monkeypatch.setitem(
        __import__("sys").modules, "fdp", type("m", (), {"catalog": {"d3d": _Handle()}})
    )
    assert (
        fdp_mod._derive_fdp_mds_location("d3d")
        == "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"
    )


def test_derive_fdp_mds_location_returns_none_without_catalog(monkeypatch):
    """No catalog (or no origin) is not fatal -- the caller falls back to Pelican."""
    monkeypatch.setitem(
        __import__("sys").modules, "fdp", type("m", (), {"catalog": {}})
    )
    assert fdp_mod._derive_fdp_mds_location("d3d") is None


@pytest.mark.parametrize(
    "setting, derived, expected",
    [
        ("pelican", "fdp://h:1/mdsip", None),          # explicit Pelican wins
        ("auto", "fdp://h:1/mdsip", "fdp://h:1/mdsip"),  # derived from catalog
        ("auto", None, None),                           # underivable -> Pelican
        ("remote://atlas.gat.com", None, "remote://atlas.gat.com"),  # verbatim
    ],
)
def test_resolve_mds_location(monkeypatch, setting, derived, expected):
    monkeypatch.setattr(fdp_mod, "_derive_fdp_mds_location", lambda device: derived)
    assert ProcessFDPConnection._resolve_mds_location(setting, "d3d") == expected


def test_factory_forwards_mds_location_to_shot_connection(monkeypatch):
    """The per-process resolution is done once and handed to every shot."""
    monkeypatch.setattr(
        fdp_mod, "_derive_fdp_mds_location", lambda device: "fdp://h:1/mdsip"
    )
    proc = ProcessFDPConnection()
    assert proc.mds_location == "fdp://h:1/mdsip"
    conn = proc.get_shot_connection(shot_id=202161)
    assert conn._mds_location == "fdp://h:1/mdsip"


def test_mds_fetch_uses_the_configured_location(monkeypatch):
    """MdsSignal is constructed with the fdp:// URL, not location=None."""
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161, mds_location="fdp://h:1/mdsip")
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    conn.get_data(r"\efit_a_eqdsk:li", tree_name="_efit_tree")
    assert _FakeMdsSignal.last_args["location"] == "fdp://h:1/mdsip"


def test_ptdata2_denylist_only_guards_the_pelican_path(monkeypatch):
    """Over fdp:// the origin evaluates the record, so the deny-list must not fire."""
    monkeypatch.setattr(fdp_mod, "_PTDATA2_BACKED_NODES", {r"\fs04"})
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)

    # Pelican tree files: TDI is client-side, so the node is refused loudly.
    pelican = FDPDataConnection(202161, mds_location=None)
    with pytest.raises(FetchDataError, match="not resolvable"):
        pelican.get_data(r"\fs04", tree_name="d3d")

    # fdp:// mdsip: fetched like any other node.
    over_fdp = FDPDataConnection(202161, mds_location="fdp://h:1/mdsip")
    np.testing.assert_array_equal(
        over_fdp.get_data(r"\fs04", tree_name="d3d"), [10.0, 20.0]
    )
