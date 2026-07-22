# FDP DataConnection (DIII-D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an FDP/Pelican signal-retrieval backend to disruption-py so DIII-D physics methods can read PTDATA pointnames and pure-stored MDSplus tree nodes (incl. EFIT) over the Fusion Data Platform, with all D3D physics methods unchanged.

**Architecture:** A new per-shot `FDPDataConnection` implements disruption-py's `DataConnection` ABC and dispatches each `get_data*` call by inspecting the `path`/`tree_name` the physics methods pass: `ptdata('name', shot)` strings route to `toksearch_d3d.PtDataSignal`; `\node` + tree route to `toksearch.MdsSignal` (Pelican, `location=None`). Tree-nickname logic is extracted into a shared `TreeNicknameMixin` reused by both `MDSConnection` and `FDPDataConnection`; `retrieval_manager`'s `isinstance(MDSConnection)` nickname check becomes duck-typed on the mixin. Selection works via the existing `connection_initializer` injection hook (no core edit) and via a config branch in `workflow.get_process_connection`.

**Tech Stack:** Python 3.11, pytest, `toksearch` / `toksearch_d3d` (FDP signal classes — **not** installed in the disruption-py dev/test env, so all imports are guarded and unit tests mock the signal classes), Dynaconf config.

**Reference spec:** `docs/superpowers/specs/2026-07-21-fdp-dataconnection-design.md`

**Out of scope (deferred):** PTDATA2/PTHEAD2-backed tree nodes — this plan only adds a *guard* that fails loudly rather than hanging. Full enumeration + reroute + calibration is a separate spec (see `../../../../PTDATA2_HANDOFF.md` in the repos dir). The SQL layer (`ShotDatabase`) is untouched.

---

## File Structure

**New files:**
- `disruption_py/inout/nickname.py` — `TreeNicknameMixin` (tree-nickname resolution shared by MDS + FDP).
- `disruption_py/inout/fdp.py` — `ProcessFDPConnection` (per-process factory) + `FDPDataConnection` (per-shot dispatcher).
- `tests/test_nickname.py` — unit tests for the mixin.
- `tests/test_fdp_connection.py` — unit tests for the FDP dispatcher (mocked signal classes).
- `tests/test_fdp_integration.py` — opt-in integration/parity test (skipped unless `toksearch_d3d` + `BEARER_TOKEN` present).
- `docs/usage/fdp.md` — how to select the FDP backend.

**Modified files:**
- `disruption_py/inout/mds.py` — `MDSConnection` inherits `TreeNicknameMixin`; remove its inline nickname methods.
- `disruption_py/core/retrieval_manager.py` — nickname `isinstance` check → `TreeNicknameMixin`; import update.
- `disruption_py/workflow.py` — `get_process_connection` gains an FDP branch (checked first, so the default stays MDS).

**Notes on two design decisions locked here:**
- **Reconnect:** `FDPDataConnection.reconnect()` is a no-op (`PtDataSignal`/`MdsSignal` are stateless per fetch). We therefore do **not** modify `retrieval_manager`'s reconnect guard — a no-op that is never called is harmless, and leaving the guard alone keeps MDS behavior identical.
- **Config selection order:** the FDP branch is checked **before** MDS in `get_process_connection`, and the shipped `d3d/config.toml` does **not** contain an `[d3d.inout.fdp]` section. A user opts in by adding that (possibly empty) section via `~/.config/disruption-py/user.toml`; because the section is absent by default, D3D still resolves to MDS. This avoids the "both keys present, which wins?" ambiguity.

---

## Task 1: Extract `TreeNicknameMixin`

**Files:**
- Create: `disruption_py/inout/nickname.py`
- Modify: `disruption_py/inout/mds.py` (class decl ~L138, remove nickname methods ~L377-399)
- Test: `tests/test_nickname.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_nickname.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nickname.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'disruption_py.inout.nickname'`

- [ ] **Step 3: Create the mixin**

Create `disruption_py/inout/nickname.py`:

```python
#!/usr/bin/env python3

"""
Shared tree-nickname mechanism for DataConnection backends.

A "nickname" (e.g. "_efit_tree") maps to a real tree name resolved lazily by a
registered zero-arg function. Both MDSConnection and FDPDataConnection reuse
this so that retrieval_manager can register the "_efit_tree" resolver against
either backend.
"""

from typing import Callable, Dict


class TreeNicknameMixin:
    """Tree-nickname resolution.

    Consumers MUST initialize ``self.tree_nickname_funcs`` and
    ``self.tree_nicknames`` (both empty dicts) in their own ``__init__``.
    """

    def add_tree_nickname_funcs(self, tree_nickname_funcs: Dict[str, Callable]):
        """Register nickname -> resolver-function mappings."""
        self.tree_nickname_funcs.update(tree_nickname_funcs)

    def get_tree_name_of_nickname(self, nickname: str):
        """Resolve a nickname to its tree name (lazily, cached), or None."""
        if nickname not in self.tree_nicknames and nickname in self.tree_nickname_funcs:
            self.tree_nicknames[nickname] = self.tree_nickname_funcs[nickname]()
        return self.tree_nicknames.get(nickname, None)

    def tree_name(self, for_name: str) -> str:
        """The tree name for ``for_name``, whether it is a nickname or a real name."""
        return self.get_tree_name_of_nickname(for_name) or for_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nickname.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Refactor `MDSConnection` to use the mixin**

In `disruption_py/inout/mds.py`:

Add the import near the other `disruption_py.inout` imports (~L18):

```python
from disruption_py.inout.base import DataConnection, ProcessConnection
from disruption_py.inout.nickname import TreeNicknameMixin
```

Change the class declaration (~L138) from:

```python
class MDSConnection(DataConnection):
```

to:

```python
class MDSConnection(TreeNicknameMixin, DataConnection):
```

Delete the three now-inherited methods from `MDSConnection` (the block ~L377-399): `add_tree_nickname_funcs`, `get_tree_name_of_nickname`, and `tree_name`. Leave `__init__` untouched (it still sets `self.tree_nickname_funcs = {}` and `self.tree_nicknames = {}`, which the mixin requires).

- [ ] **Step 6: Verify MDS behavior is unchanged**

Run: `python -m pytest tests/test_nickname.py -v`
Expected: PASS

Run: `python -c "from disruption_py.inout.mds import MDSConnection; from disruption_py.inout.nickname import TreeNicknameMixin; assert issubclass(MDSConnection, TreeNicknameMixin); print('ok')"`
Expected: prints `ok`

- [ ] **Step 7: Commit**

```bash
git add disruption_py/inout/nickname.py disruption_py/inout/mds.py tests/test_nickname.py
git commit -m "refactor(inout): extract TreeNicknameMixin shared by MDS/FDP backends"
```

---

## Task 2: Scaffold the FDP module (imports, factory, lifecycle)

**Files:**
- Create: `disruption_py/inout/fdp.py`
- Test: `tests/test_fdp_connection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fdp_connection.py`:

```python
"""Unit tests for the FDP data connection (signal classes are mocked)."""

import numpy as np
import pytest

import disruption_py.inout.fdp as fdp_mod
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fdp_connection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'disruption_py.inout.fdp'`

- [ ] **Step 3: Create the module skeleton**

Create `disruption_py/inout/fdp.py`:

```python
#!/usr/bin/env python3

"""
FDP / Pelican signal-retrieval backend for disruption-py (DIII-D).

FDPDataConnection satisfies the DataConnection interface used by the D3D
physics methods, dispatching each get_data* call:
  * ptdata('name', shot) strings  -> toksearch_d3d.PtDataSignal
  * \\node + tree_name            -> toksearch.MdsSignal (Pelican, location=None)

toksearch / toksearch_d3d are NOT dependencies of the disruption-py dev/test
environment, so their imports are guarded; unit tests patch the module globals
PtDataSignal / MdsSignal with mocks.
"""

import re
from typing import Any, List, Tuple

import numpy as np
from loguru import logger

from disruption_py.config import config
from disruption_py.core.physics_method.errors import FetchDataError, NanDataError
from disruption_py.core.utils.misc import shot_msg
from disruption_py.core.utils.shared_instance import SharedInstance
from disruption_py.inout.base import DataConnection, ProcessConnection
from disruption_py.inout.nickname import TreeNicknameMixin
from disruption_py.machine.tokamak import Tokamak

try:
    from toksearch import MdsSignal
    from toksearch_d3d import PtDataSignal
except ModuleNotFoundError:
    # Guarded: the FDP signal stack may be absent (e.g. in CI). Real use runs
    # inside an FDP-configured env (`fdp run` / setup_environment()).
    MdsSignal = None
    PtDataSignal = None

# ptdata('name', shot) or ptdata("name", shot) -- captures the pointname.
_PTDATA_RE = re.compile(r"""^\s*ptdata\(\s*['"]([^'"]+)['"]\s*,.*\)\s*$""", re.IGNORECASE)

# Deny-list of MDSplus nodes whose TDI record calls PTDATA2/PTHEAD2 and would
# HANG (not error) in the Pelican/XRootD env. Deferred to the PTDATA2 follow-up
# spec; kept empty here except as a guard hook. See PTDATA2_HANDOFF.md.
_PTDATA2_BACKED_NODES: set = set()


def _parse_ptdata_pointname(path: str):
    """Return the PTDATA pointname if ``path`` is a ptdata(...) expression, else None."""
    match = _PTDATA_RE.match(path)
    return match.group(1) if match else None


class ProcessFDPConnection(ProcessConnection):
    """
    Process-level FDP connection factory.

    Holds no heavy per-process state: PtDataSignal reuses a per-process
    PtDataReader internally (toksearch_d3d >= 0.10.0) and MdsSignal opens trees
    per fetch. ``options`` is reserved for future config knobs.
    """

    def __init__(self, **options: Any):
        self.options = options

    @classmethod
    def from_config(cls, tokamak: Tokamak) -> "ProcessFDPConnection":
        """Create a shared per-process instance from the [<tokamak>.inout.fdp] config."""
        fdp_cfg = config(tokamak).inout.get("fdp", {}) or {}
        return SharedInstance(ProcessFDPConnection).get_instance(**dict(fdp_cfg))

    def get_shot_connection(self, shot_id: int) -> "FDPDataConnection":
        """Create a per-shot FDP data connection."""
        return FDPDataConnection(shot_id)


class FDPDataConnection(TreeNicknameMixin, DataConnection):
    """Per-shot FDP data connection dispatching to PtDataSignal / MdsSignal."""

    def __init__(self, shot_id: int):
        self._shot_id = shot_id
        self.tree_nickname_funcs = {}
        self.tree_nicknames = {}

    @property
    def shot_id(self) -> int:
        """The shot ID this connection is bound to."""
        return self._shot_id

    def cleanup(self) -> None:
        """No persistent per-shot state to release (fetches are stateless)."""

    def reconnect(self) -> None:
        """No-op: PtDataSignal / MdsSignal are stateless per fetch."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fdp_connection.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add disruption_py/inout/fdp.py tests/test_fdp_connection.py
git commit -m "feat(inout): scaffold FDP connection factory + per-shot lifecycle"
```

---

## Task 3: PTDATA dispatch

**Files:**
- Modify: `disruption_py/inout/fdp.py` (add `get_data`, `get_data_with_dims`, `get_dims`)
- Test: `tests/test_fdp_connection.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fdp_connection.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fdp_connection.py -k ptdata -v`
Expected: FAIL with `AttributeError: 'FDPDataConnection' object has no attribute 'get_data'`

- [ ] **Step 3: Implement the dispatch core + PTDATA path**

Add these methods and a private helper to `FDPDataConnection` in `disruption_py/inout/fdp.py` (below `reconnect`):

```python
    # --- internal dispatch ---------------------------------------------------

    @staticmethod
    def _ordered_dim_names(dim_nums: List[int]) -> Tuple[str, ...]:
        """Positional dim labels covering the requested indices (MDSplus dim_of order)."""
        return tuple(f"dim{i}" for i in range(max(dim_nums) + 1))

    def _fetch(self, path: str, tree_name: str, dim_nums: List[int]):
        """Fetch ``path`` and return (result_dict, ordered_dim_names)."""
        pointname = _parse_ptdata_pointname(path)
        if pointname is not None:
            logger.trace(
                shot_msg("FDP ptdata fetch: {p}"), shot=self._shot_id, p=pointname
            )
            result = PtDataSignal(pointname).fetch(self._shot_id)
            return result, ("times",)

        if path in _PTDATA2_BACKED_NODES:
            raise FetchDataError(
                f"{path!r}: PTDATA2-backed node is not yet supported over FDP "
                "(would hang in the Pelican/XRootD env). Deferred to the PTDATA2 "
                "follow-up; see PTDATA2_HANDOFF.md."
            )

        resolved_tree = self.tree_name(tree_name)
        dim_names = self._ordered_dim_names(dim_nums)
        logger.trace(
            shot_msg("FDP mds fetch: {p} @ {t}"),
            shot=self._shot_id,
            p=path,
            t=resolved_tree,
        )
        result = MdsSignal(path, resolved_tree, location=None, dims=dim_names).fetch(
            self._shot_id
        )
        return result, dim_names

    # --- DataConnection interface -------------------------------------------

    def get_data(
        self,
        path: str,
        group: str = None,
        required: bool = False,
        tree_name: str = None,
        **kwargs,
    ) -> np.ndarray:
        """Get data at ``path`` (ptdata pointname or MDSplus tree node)."""
        tree_name = tree_name or group
        result, _ = self._fetch(path, tree_name, [0])
        data = result["data"]
        if required and not np.any(np.isfinite(data)):
            raise NanDataError(path)
        return data

    def get_data_with_dims(
        self,
        path: str,
        group: str = None,
        required: bool = False,
        dim_nums: List = None,
        tree_name: str = None,
        **kwargs,
    ) -> Tuple:
        """Get data and the requested dimension arrays."""
        tree_name = tree_name or group
        dim_nums = dim_nums or [0]
        result, dim_names = self._fetch(path, tree_name, dim_nums)
        data = result["data"]
        dims = [result[dim_names[d]] for d in dim_nums]
        if required and not np.any(np.isfinite(data)):
            raise NanDataError(path)
        return (data, *dims)

    def get_dims(
        self,
        path: str,
        group: str = None,
        dim_nums: List = None,
        tree_name: str = None,
        **kwargs,
    ) -> Tuple:
        """Get only the requested dimension arrays."""
        tree_name = tree_name or group
        dim_nums = dim_nums or [0]
        result, dim_names = self._fetch(path, tree_name, dim_nums)
        return tuple(result[dim_names[d]] for d in dim_nums)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fdp_connection.py -k "ptdata or required" -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add disruption_py/inout/fdp.py tests/test_fdp_connection.py
git commit -m "feat(inout): FDP PTDATA dispatch via PtDataSignal"
```

---

## Task 4: MDSplus tree-node dispatch (incl. nickname resolution)

**Files:**
- Test: `tests/test_fdp_connection.py`

(The implementation in Task 3 already covers the MDS path via `_fetch`; this task adds the tests that pin its behavior — nickname resolution, `location=None`, dim mapping.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fdp_connection.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `python -m pytest tests/test_fdp_connection.py -k mds -v`
Expected: PASS (3 passed) — the Task 3 implementation already handles this. If any fail, fix `_fetch`/`get_*` before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fdp_connection.py
git commit -m "test(inout): pin FDP MDSplus tree-node dispatch + nickname resolution"
```

---

## Task 5: Multi-dimension (`dim_nums`) mapping

**Files:**
- Test: `tests/test_fdp_connection.py`

The `_ordered_dim_names` helper sizes the `dims` tuple to `max(dim_nums)+1` so a call like `get_data_with_dims(..., dim_nums=[2])` (e.g. `\psirz`) forwards `dims=("dim0","dim1","dim2")` to `MdsSignal` and returns the index-2 array — replicating MDSplus `dim_of(sig, 2)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fdp_connection.py`:

```python
def test_higher_dim_nums_sizes_dims_and_returns_requested(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    data, dim2 = conn.get_data_with_dims(
        r"\top.results.geqdsk:psirz", tree_name="_efit_tree", dim_nums=[2]
    )
    # dims tuple must cover index 2
    assert _FakeMdsSignal.last_args["dims"] == ("dim0", "dim1", "dim2")
    # _FakeMdsSignal returns [i, i+0.5] for dimN -> index 2 => [2.0, 2.5]
    np.testing.assert_array_equal(dim2, [2.0, 2.5])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_fdp_connection.py -k higher_dim -v`
Expected: PASS (already supported by `_ordered_dim_names`)

- [ ] **Step 3: Add a real-signal validation note**

Add this comment directly above the `MdsSignal(...)` call in `_fetch` in `disruption_py/inout/fdp.py`:

```python
        # NOTE: multi-dim (e.g. \psirz, dim_nums=[2]) forwards positional dim
        # names sized to the request. MdsSignal's data_order/shape handling for
        # >1-D nodes must be validated against real data (Task 9 integration);
        # if the axis order differs, thread a data_order kwarg through here.
```

- [ ] **Step 4: Commit**

```bash
git add disruption_py/inout/fdp.py tests/test_fdp_connection.py
git commit -m "feat(inout): FDP multi-dim dim_nums mapping (mirrors MDSplus dim_of)"
```

---

## Task 6: PTDATA2-backed-node guard (fail loud, never hang)

**Files:**
- Test: `tests/test_fdp_connection.py`

The `_PTDATA2_BACKED_NODES` deny-list + the check in `_fetch` (both added in Task 3) make a known-bad node raise `FetchDataError` instead of hanging. This task pins that behavior so the deferred surface is safe.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fdp_connection.py`:

```python
def test_ptdata2_backed_node_raises_not_hangs(monkeypatch):
    monkeypatch.setattr(fdp_mod, "MdsSignal", _FakeMdsSignal)
    # Simulate a node enumerated as PTDATA2-backed (deferred work).
    monkeypatch.setattr(fdp_mod, "_PTDATA2_BACKED_NODES", {r"\some_ptdata2_node"})
    conn = FDPDataConnection(202161)
    conn.add_tree_nickname_funcs({"_efit_tree": lambda: "efit01"})
    from disruption_py.core.physics_method.errors import FetchDataError
    with pytest.raises(FetchDataError, match="PTDATA2"):
        conn.get_data(r"\some_ptdata2_node", tree_name="_efit_tree")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_fdp_connection.py -k ptdata2 -v`
Expected: PASS

- [ ] **Step 3: Run the full FDP + nickname unit suite**

Run: `python -m pytest tests/test_fdp_connection.py tests/test_nickname.py -v`
Expected: PASS (all)

- [ ] **Step 4: Commit**

```bash
git add tests/test_fdp_connection.py
git commit -m "test(inout): guard PTDATA2-backed nodes fail loudly (deferred surface safe)"
```

---

## Task 7: Duck-type the `retrieval_manager` nickname check

**Files:**
- Modify: `disruption_py/core/retrieval_manager.py:15` (import), `:145` (isinstance)

- [ ] **Step 1: Update the import**

In `disruption_py/core/retrieval_manager.py`, change line 15 from:

```python
from disruption_py.inout.mds import MDSConnection, mdsExceptions
```

to:

```python
from disruption_py.inout.mds import mdsExceptions
from disruption_py.inout.nickname import TreeNicknameMixin
```

- [ ] **Step 2: Duck-type the nickname registration**

Change the check (~L145) from:

```python
        if isinstance(data_conn, MDSConnection):
```

to:

```python
        if isinstance(data_conn, TreeNicknameMixin):
```

(Leave the `mdsExceptions.MDSplusERROR` reconnect guards at ~L103/L114 unchanged — see the "Reconnect" note in the header.)

- [ ] **Step 3: Verify it imports and MDS still qualifies**

Run:
```bash
python -c "import disruption_py.core.retrieval_manager as r; from disruption_py.inout.mds import MDSConnection; from disruption_py.inout.nickname import TreeNicknameMixin; assert issubclass(MDSConnection, TreeNicknameMixin); print('ok')"
```
Expected: prints `ok`

- [ ] **Step 4: Run the existing unit suite for regressions**

Run: `python -m pytest tests/test_nickname.py tests/test_fdp_connection.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add disruption_py/core/retrieval_manager.py
git commit -m "refactor(core): duck-type nickname registration on TreeNicknameMixin"
```

---

## Task 8: Wire `get_process_connection` config branch

**Files:**
- Modify: `disruption_py/workflow.py` (import + `get_process_connection` ~L238-253)
- Test: `tests/test_fdp_connection.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fdp_connection.py`:

```python
def test_get_process_connection_selects_fdp(monkeypatch):
    import disruption_py.workflow as wf
    from disruption_py.machine.tokamak import Tokamak

    class _Cfg:
        # mimic Dynaconf: 'inout' with only 'fdp' present
        inout = {"fdp": {}}

    monkeypatch.setattr(wf, "resolve_tokamak_from_environment", lambda t: Tokamak.D3D)
    monkeypatch.setattr(wf, "config", lambda tok: _Cfg())
    # ProcessFDPConnection.from_config calls fdp_mod.config directly; patch it too
    # so the test does no real Dynaconf/file I/O.
    monkeypatch.setattr(fdp_mod, "config", lambda tok: _Cfg())
    conn = wf.get_process_connection(Tokamak.D3D)
    assert isinstance(conn, ProcessFDPConnection)


def test_get_process_connection_defaults_to_mds_when_no_fdp(monkeypatch):
    import disruption_py.workflow as wf
    from disruption_py.machine.tokamak import Tokamak

    class _Cfg:
        inout = {"mds": {"mdsplus_connection_string": None}}

    monkeypatch.setattr(wf, "resolve_tokamak_from_environment", lambda t: Tokamak.D3D)
    monkeypatch.setattr(wf, "config", lambda tok: _Cfg())
    conn = wf.get_process_connection(Tokamak.D3D)
    # ProcessMDSConnection, not FDP
    assert not isinstance(conn, ProcessFDPConnection)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fdp_connection.py -k get_process_connection -v`
Expected: FAIL (`test_get_process_connection_selects_fdp` raises `ValueError: No valid ...` because there is no FDP branch yet)

- [ ] **Step 3: Add the FDP branch (checked first)**

In `disruption_py/workflow.py`, add the import alongside the other inout imports:

```python
from disruption_py.inout.fdp import ProcessFDPConnection
```

Change `get_process_connection` (~L246-253) from:

```python
    inout_cfg = config(tokamak).inout
    if "mds" in inout_cfg:
        return ProcessMDSConnection.from_config(tokamak=tokamak)

    if "xarray" in inout_cfg:
        return ProcessXarrayConnection.from_config(tokamak=tokamak)

    raise ValueError("No valid MDSplus or xarray connection found.")
```

to:

```python
    inout_cfg = config(tokamak).inout
    # FDP is checked first: it is only present when a user opts in via
    # [<tokamak>.inout.fdp] in user.toml, so the shipped D3D default (mds) wins
    # unless explicitly overridden.
    if "fdp" in inout_cfg:
        return ProcessFDPConnection.from_config(tokamak=tokamak)

    if "mds" in inout_cfg:
        return ProcessMDSConnection.from_config(tokamak=tokamak)

    if "xarray" in inout_cfg:
        return ProcessXarrayConnection.from_config(tokamak=tokamak)

    raise ValueError("No valid FDP, MDSplus, or xarray connection found.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fdp_connection.py -k get_process_connection -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add disruption_py/workflow.py tests/test_fdp_connection.py
git commit -m "feat(workflow): select FDP backend via [inout.fdp] config (checked first)"
```

---

## Task 9: Opt-in integration / parity test (BEARER_TOKEN-gated)

**Files:**
- Create: `tests/test_fdp_integration.py`

This test is **skipped by default** and only runs where `toksearch_d3d` is installed and `BEARER_TOKEN` is set (i.e. inside an FDP environment). It asserts **closeness**, not exact equality, and uses parameters that do **not** depend on PTDATA2-backed nodes.

- [ ] **Step 1: Write the integration test**

Create `tests/test_fdp_integration.py`:

```python
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
```

- [ ] **Step 2: Verify it is skipped (no FDP env)**

Run: `python -m pytest tests/test_fdp_integration.py -v`
Expected: SKIPPED (toksearch_d3d not importable → `pytest.importorskip` skips the module)

- [ ] **Step 3: (Manual, in an FDP env) run the real thing**

Run: `fdp run python -m pytest tests/test_fdp_integration.py -v`
Expected: PASS (2 passed). If `\psirz`-style multi-dim reads are exercised later and axis order is wrong, thread a `data_order` kwarg through `_fetch` per the Task 5 note.

- [ ] **Step 4: Commit**

```bash
git add tests/test_fdp_integration.py
git commit -m "test(inout): opt-in FDP integration/parity test (BEARER_TOKEN-gated)"
```

---

## Task 10: Usage documentation

**Files:**
- Create: `docs/usage/fdp.md`
- Modify: `mkdocs.yml` (nav — add the page)

- [ ] **Step 1: Write the usage page**

Create `docs/usage/fdp.md`:

```markdown
# Reading DIII-D data over FDP (Pelican)

disruption-py can retrieve DIII-D signals through the Fusion Data Platform
(FDP / Pelican object store) instead of a direct `atlas` MDSplus connection.
This lets you run off-site with only an FDP bearer token.

**Prerequisites:** an FDP-configured environment. Run your script under
`fdp run` (or apply `setup_environment()`), which sets `PTDATA_LOC`, the Pelican
tree paths, and the bearer token. `toksearch` and `toksearch_d3d` must be
installed in that environment.

## Option A — inject the backend (no config change)

```python
from disruption_py.workflow import get_shots_data
from disruption_py.inout.fdp import ProcessFDPConnection

data = get_shots_data(
    shotlist_setting=[202161],
    connection_initializer=ProcessFDPConnection.from_config,
    # ... your other settings ...
)
```

Run it: `fdp run python your_script.py`

## Option B — select FDP via config

Add to `~/.config/disruption-py/user.toml`:

```toml
[d3d.inout.fdp]
```

With that section present, `get_process_connection` selects the FDP backend for
D3D. Remove it to fall back to the default `atlas` MDSplus connection.

## Scope & limitations

- Covers PTDATA pointnames and pure-stored MDSplus tree nodes, including the
  EFIT trees.
- **PTDATA2/PTHEAD2-backed tree nodes are not yet supported** — they would hang
  in the Pelican/XRootD environment, so the backend raises a clear error for any
  node on its internal deny-list rather than hanging. Full support is planned
  separately.
- The SQL layer (disruption times, shot lists) is unchanged — provide it as you
  do today, or use `DummyDatabase`.
```

- [ ] **Step 2: Add to nav**

In `mkdocs.yml`, under the `usage` nav section, add an entry pointing to `usage/fdp.md` (match the existing nav style — e.g. `- FDP (Pelican): usage/fdp.md`).

- [ ] **Step 3: Verify docs build (if mkdocs available)**

Run: `python -m mkdocs build --strict 2>&1 | tail -5` (skip if mkdocs is not installed)
Expected: build succeeds, or is skipped.

- [ ] **Step 4: Commit**

```bash
git add docs/usage/fdp.md mkdocs.yml
git commit -m "docs: how to read DIII-D data over FDP"
```

---

## Final verification

- [ ] **Run the full new unit suite**

Run: `python -m pytest tests/test_nickname.py tests/test_fdp_connection.py tests/test_fdp_integration.py -v`
Expected: nickname + fdp_connection PASS; fdp_integration SKIPPED (no FDP env).

- [ ] **Run the broader suite for regressions**

Run: `python -m pytest tests/ -q`
Expected: no new failures vs. the pre-change baseline (some tests may already require MDSplus/DB and skip or xfail — compare against baseline, don't assume green).

- [ ] **Confirm the injection path end-to-end (manual, FDP env)**

Run a small `get_shots_data(..., connection_initializer=ProcessFDPConnection.from_config)` script under `fdp run` for one shot and confirm core D3D parameters (Ip, βN, li, kappa) populate.

---

## Self-review notes (coverage vs. spec)

- **Dispatch (ptdata / tree / nickname / dim_nums / required)** → Tasks 3–5.
- **TreeNicknameMixin in `inout/nickname.py`** → Task 1.
- **Duck-typed retrieval_manager check** → Task 7.
- **Injection + config-branch selection** → Task 8 (config) + Task 10 (docs show both).
- **PTDATA2 guard (fail loud, deferred elsewhere)** → Task 6.
- **Closeness parity test, BEARER_TOKEN-gated** → Task 9.
- **Reconnect** → intentionally a no-op; guard left unchanged (header note).
- **Multi-dim `data_order` for `\psirz`** → mechanism in Task 5; real-axis-order validation flagged for the Task 9 integration run (the one place reality may force a small follow-up).
```
