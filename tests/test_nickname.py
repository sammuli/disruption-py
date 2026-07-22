"""Unit tests for the shared tree-nickname mixin."""

from disruption_py.inout.nickname import TreeNicknameMixin


class _Conn(TreeNicknameMixin):
    def __init__(self):
        self.tree_nickname_funcs = {}
        self.tree_nicknames = {}


def test_resolves_nickname_via_registered_func():
    conn = _Conn()
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    assert conn.get_tree_name_of_nickname("_efit_tree") == "efit01"
    assert conn.tree_name("_efit_tree") == "efit01"


def test_func_called_once_and_cached():
    conn = _Conn()
    calls = []
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: (calls.append(1), "efit01")[1]})
    conn.get_tree_name_of_nickname("_efit_tree")
    conn.get_tree_name_of_nickname("_efit_tree")
    assert len(calls) == 1


def test_unknown_name_passes_through():
    conn = _Conn()
    assert conn.get_tree_name_of_nickname("d3d") is None
    assert conn.tree_name("d3d") == "d3d"
