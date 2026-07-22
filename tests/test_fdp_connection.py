"""Unit tests for the FDP data connection (signal classes are mocked)."""

import numpy as np
import pytest

import disruption_py.inout.fdp as fdp_mod
from disruption_py.core.physics_method.errors import NanDataError
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
