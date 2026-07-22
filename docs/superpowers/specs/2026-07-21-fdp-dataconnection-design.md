# FDP `DataConnection` for disruption-py (DIII-D) — Design

**Date:** 2026-07-21
**Status:** Design — approved for planning
**Scope:** DIII-D signal retrieval over the Fusion Data Platform (FDP / Pelican) for PTDATA pointnames and pure-stored MDSplus tree nodes (incl. EFIT). PTDATA2-backed nodes deferred to a follow-up spec.

## Goal

Let disruption-py read DIII-D signal data through FDP (Pelican/OSDF object store) instead of a direct atlas MDSplus connection, so the tool can run off-site with only an FDP bearer token. This is delivered as a new signal-retrieval backend implementing disruption-py's existing `DataConnection` / `ProcessConnection` abstractions.

**Target:** D3D parity for the two tractable data shapes — **PTDATA pointnames** (via `PtDataSignal`) and **pure-stored MDSplus tree nodes including the EFIT trees** (via `MdsSignal` over Pelican). One class of node — **PTDATA2/PTHEAD2-backed tree nodes** — is a known gap that hangs in the Pelican/XRootD environment and is **deferred to its own follow-up spec** (see "Deferred work" below). This spec is complete and implementable without it; that follow-up closes the remaining parity gap.

## Non-goals

- **SQL layer is out of scope.** disruption-py's `ShotDatabase` (d3drdb via pyodbc/FreeTDS — disruption times, shot lists, EFIT run-tree lookups) is a separate component. Users continue to get that data as they do today (direct d3drdb access, or `DummyDatabase`). SQL-over-FDP is a possible follow-on.
- No changes to any D3D physics method. The backend must satisfy the existing `path` / `tree_name` conventions those methods already pass.
- Other machines (CMOD, EAST, HBTEP, MAST) are untouched.
- **PTDATA2/PTHEAD2-backed tree nodes are deferred** to a separate follow-up spec. This spec delivers everything else; the follow-up handles node enumeration and calibration replication.

## Background: how D3D data access works today

The data-access layer lives in `disruption_py/inout/`. Two ABCs (`inout/base.py`):

- `DataConnection` (per-shot): `get_data(path, group=None, required=False, **kw)`, `get_data_with_dims(path, group=None, required=False, dim_nums=None, **kw)`, `get_dims(path, group=None, dim_nums=None, **kw)`, `cleanup()`, optional `reconnect()`.
- `ProcessConnection` (per-process factory): `get_shot_connection(shot_id)`, classmethod `from_config(tokamak)`.

Concrete backends: `MDSConnection` (`inout/mds.py`, used by D3D/CMOD/EAST/HBTEP) and `XarrayDataConnection` (`inout/xr.py`, MAST zarr-over-S3). Backend selection is a config-driven if-ladder in `workflow.get_process_connection`, and `get_shots_data` also accepts a `connection_initializer` callable that injects a custom `ProcessConnection` factory **without any core edit**.

**The key constraint:** on D3D the physics methods pass MDSplus-native strings straight through `get_data`. Three shapes occur:

1. **PTDATA as a TDI expression** — `params.get_data_with_dims(f"ptdata('ip', {params.shot_id})")` (no `tree_name`).
2. **Tree node + tree name** — `params.get_data(r"\efit_a_eqdsk:li", tree_name="_efit_tree")`, `params.get_data_with_dims(r"\top.nb:pinj", tree_name="d3d")`, etc.
3. **The `_efit_tree` nickname** — resolved at runtime to a real EFIT tree.

Why not just repoint `MDSConnection` at Pelican-hosted trees? Because evaluating a `ptdata(...)` TDI expression inside the Pelican/XRootD environment **hangs** (fork-after-threading: MDSplus's `LibCallg` forks a child that inherits broken XRootD mutex state). PTDATA must therefore be fetched through FDP's `PtDataSignal` / `PtDataReader` directly, never as MDSplus TDI. This is the reason a bespoke backend — not a reconfigured `MDSConnection` — is required.

### EFIT nickname resolution is SQL-based, not MDSplus-based

The `_efit_tree` nickname is registered in `retrieval_manager.shot_setup` and resolved by `retrieval_settings.efit_nickname_setting`. The D3D default (`DisruptionNicknameSetting._d3d_nickname`) resolves the tree from the **SQL database** (`code_rundb.dbo.plasmas`), and falls back to the static `"efit01"` tree when no row/DB is available. It does **not** issue MDSplus queries through `data_conn`. Consequences:

- The FDP connection never needs a working raw-TDI `.get()`.
- With no real d3drdb (`DummyDatabase`), `_efit_tree` resolves to `"efit01"`, which is on the Pelican origin — EFIT works out of the box for the signals-only scope.

## FDP signal APIs (the mapping targets)

- `PtDataSignal(pointname, remote=True).fetch(shot)` → `{'data', 'times' (ms), 'units'}`.
- `MdsSignal(expression, treename, location=None, dims=('times',)).fetch(shot)` → `{'data', 'times' (ms), 'units', ...extra dims}`. `location=None` reads the tree path from the environment, which `fdp run` sets to the Pelican URL list. All DIII-D MDSplus trees (not just efit01) are on the Pelican origin.

Both return times in **milliseconds**, matching what the D3D physics methods already assume — no unit conversion in the backend.

## Design

### New / changed files

| Piece | Location | Role |
|---|---|---|
| `TreeNicknameMixin` | **`disruption_py/inout/nickname.py`** (new) | Holds the tree-nickname logic (`add_tree_nickname_funcs`, `get_tree_name_of_nickname`, `tree_name`, `tree_nickname_funcs`/`tree_nicknames` state) extracted from `MDSConnection`. Mixed into both `MDSConnection` and `FDPDataConnection`. |
| `ProcessFDPConnection(ProcessConnection)` | `disruption_py/inout/fdp.py` (new) | Per-process factory. Owns a reused `PtDataReader` (matches `toksearch_d3d` 0.10.0 per-process reader reuse). Provides both a plain constructor (for injection) and `from_config(tokamak)`. |
| `FDPDataConnection(TreeNicknameMixin, DataConnection)` | `disruption_py/inout/fdp.py` (new) | Per-shot dispatcher: `get_data` / `get_data_with_dims` / `get_dims` / `cleanup` / `reconnect`. |
| nickname extraction | `disruption_py/inout/mds.py` | `MDSConnection` now inherits nickname methods from `TreeNicknameMixin` instead of defining them inline. Behavior unchanged. |
| capability checks | `disruption_py/core/retrieval_manager.py` | Two `isinstance(data_conn, MDSConnection)` checks become duck-typed (see below). |
| config branch | `disruption_py/workflow.py` | `get_process_connection` gains an `elif "fdp" in inout_cfg` branch. |
| opt-in config | `disruption_py/machine/d3d/config.toml` | An `[d3d.inout.fdp]` section (default stays `[d3d.inout.mds]`). |

### Dispatch rules (`FDPDataConnection`)

Each `get_data*` call is routed by inspecting `path` and `tree_name`/`group`:

1. **PTDATA path** — when `tree_name`/`group` is absent **and** `path` matches `ptdata(<quote>NAME<quote>, <shot>)`: extract `NAME` with a small regex, call `PtDataSignal(NAME, remote=True).fetch(shot)`.
   - `get_data` → `result['data']`
   - `get_data_with_dims` → `(result['data'], result['times'])`
   - `get_dims` → `(result['times'],)`
2. **MDSplus tree node** — when `tree_name`/`group` is present: resolve the nickname via the mixin (`_efit_tree` → real tree name), then `MdsSignal(path, resolved_tree, location=None).fetch(shot)`.
   - `get_data` → `result['data']`
   - `get_data_with_dims` → `(result['data'], *dims_for(dim_nums))`
   - `get_dims` → `dims_for(dim_nums)`
3. **`dim_nums`** map onto MdsSignal's ordered dimension arrays: dim 0 = `times`; higher indices (e.g. `\psirz` with `dim_nums=[2]`) map to the additional spatial dimensions MdsSignal returns.

`required=True` preserves the existing semantics: raise `NanDataError` when the returned data is entirely non-finite.

A small internal helper isolates the PTDATA pointname; everything with a `tree_name` is treated as a tree TDI expression. Because the dispatcher honors the exact `path` / `tree_name` / `dim_nums` / `required` contract, **no D3D physics method changes.**

### Core edits (minimal)

- **`retrieval_manager.py`:**
  - Nickname registration (~L145): `if isinstance(data_conn, MDSConnection)` → `if isinstance(data_conn, TreeNicknameMixin)`. Both `MDSConnection` and `FDPDataConnection` qualify.
  - Reconnect-on-error (~L103, ~L114): currently gated on `isinstance(e, mdsExceptions.MDSplusERROR)`. Broaden so FDP-originated errors also trigger `data_conn.reconnect()` — e.g. call `reconnect()` for any connection exposing it, or add an FDP exception type to the guard. Exact predicate decided in the plan.
- **`workflow.get_process_connection`:** add `elif "fdp" in inout_cfg: return ProcessFDPConnection.from_config(tokamak=tokamak)` after the existing `mds` / `xarray` branches.
- **`machine/d3d/config.toml`:** add an `[d3d.inout.fdp]` section (opt-in; `mds` remains the shipped default so existing behavior is unchanged).

The **injection path needs no core edit**: a user passes `connection_initializer=ProcessFDPConnection.from_config` (or a lambda) to `get_shots_data` and gets the FDP backend immediately. The config branch is the eventual first-class UX.

### Lifecycle, environment & auth

- The process is assumed to run in an FDP-configured environment (`fdp run python …`, or `setup_environment()` already applied). The backend does **not** shell out to the `fdp` CLI. `PtDataSignal(remote=True)` and `MdsSignal(location=None)` then resolve Pelican URLs and the bearer token from the environment.
- `ProcessFDPConnection` builds one `PtDataReader` per worker process; each per-shot `FDPDataConnection` reuses it. This mirrors the existing MDS per-process-connection pattern and TokSearch's per-process reader reuse.
- `cleanup()` is effectively a no-op (no persistent per-shot trees to close). `reconnect()` rebuilds/clears the reader and any cached nickname state.

### Selection UX

Per the "both" decision: the classes work via `connection_initializer` injection on day one, and a config branch (`[d3d.inout.fdp]` + `workflow` if-ladder entry, selectable via `user.toml` / `DISPY` env) gives the in-tree experience for the eventual merge.

### Fetch-error contract (MdsException-compatible) — added during implementation

The D3D physics methods catch **`mdsExceptions.MdsException`** at ~30 sites to drive per-signal fallbacks (e.g. try `ptdata('ipsip')`, on `MdsException` fall back to `ptdata('ipspr15v')`; try an EFIT node, on failure use a default). For the "physics methods unchanged" promise to hold, the FDP backend's fetch failures must be catchable by those same handlers — but `MdsSignal` only raises real `mdsExceptions` on the tree path, and `PtDataSignal` raises its own (non-MDS) errors, so a plain `FetchDataError` wrapper would silently disable every fallback (→ all-NaN instead of the fallback value).

Resolution: `FDPDataConnection._fetch` wraps all fetch failures (ptdata path, mds path, and the PTDATA2 deny-list raise) in a **dual-typed** exception:

```python
class FdpFetchError(FetchDataError, mdsExceptions.MdsException): ...
```

so a single object is caught by both `except mdsExceptions.MdsException` (physics fallbacks fire unchanged) and `except FetchDataError` / `except DataError` (generic disruption-py handlers). `NanDataError` (the `required=True` path) is left as-is — it is `DataError`, not `MdsException`, matching MDS behavior exactly.

**Real-env validation required:** the dual-typing subclasses the *real* MDSplus `MdsException` when MDSplus is installed (the FDP runtime). This is verified here only against the dummy `mdsExceptions` stub (MDSplus absent in the dev/test env). The integration run must confirm (a) `FdpFetchError` constructs cleanly under real MDSplus and (b) a real physics fallback (e.g. `ipsip`→`ipspr15v`) actually fires over FDP.

## Deferred work — PTDATA2-backed MDSplus nodes (separate follow-up spec)

**Not in scope for this spec.** It is recorded here so the boundary is explicit, and will be brainstormed as its own spec → plan → implementation cycle.

The problem: some DIII-D MDSplus tree nodes are stored as TDI expressions that internally call `PTDATA2` / `PTHEAD2` (often a calibrated wrapper over a raw PTDATA pointname). When `MdsSignal` fetches such a node, MDSplus evaluates that TDI **client-side** in the local process — triggering the same fork-after-threading **hang** in the Pelican/XRootD environment that blocks direct `ptdata(...)` TDI. Routing these nodes to `MdsSignal` will hang, not error, so they cannot silently be treated as ordinary tree nodes. There are definitely such nodes among those the D3D physics methods read.

The follow-up spec will own (rough sketch, to be designed there, not here):

1. **Enumerating** the offending nodes among the paths disruption-py's D3D methods touch (nodes whose record resolves to `PTDATA2`/`PTHEAD2`).
2. **Rerouting** them in the dispatcher — look up the underlying pointname (from the node's TDI record or a curated node→pointname map) and fetch via `PtDataSignal`, mirroring the documented FDP workaround (`\ECPTNAMF[0]` → pointname → `PtDataSignal`).
3. **Replicating calibration** where the tree node applied calibration on top of raw PTDATA — not always a 1:1 substitution.

**Boundary for *this* spec:** the dispatcher must **fail loudly, not hang**, if it is ever handed such a node. The plan should include a cheap guard (e.g. a small deny-list of known PTDATA2-backed nodes, or a documented constraint) that raises a clear error rather than letting a fetch hang — so the deferred surface is safe, not a silent trap. Full enumeration and the reroute/calibration machinery belong to the follow-up.

## Other coverage risks (validate during implementation)

- **2-D nodes** — `\psirz` with `dim_nums=[2]`: confirm `MdsSignal` dimension ordering matches what `get_dims` / the physics method expects.
- **Non-`efit01` EFIT trees** — when a real d3drdb is present, disruption/run-specific trees (e.g. `efit18`) must be available on the Pelican origin.

## Testing

- **Unit** — dispatch/parsing table with mocked `PtDataSignal` / `MdsSignal`: PTDATA pointname extraction, tree-node routing, nickname resolution, `dim_nums` handling, `required=True` → `NanDataError`.
- **Integration** (requires `BEARER_TOKEN`) — fetch a small set of known shots through the FDP backend via injection and compare a handful of parameters (Ip, βN, li, kappa) against the atlas `MDSConnection` for the same shots. **Assert closeness, not exact equality** (tolerance-based), to absorb benign backend/interpolation differences. Choose parameters that do **not** depend on PTDATA2-backed nodes (deferred). *Status:* the shipped `tests/test_fdp_integration.py` is currently a **sanity** test (finiteness + magnitude bounds for Ip and li), not yet a true atlas-vs-FDP parity comparison — the full parity comparison needs MDSplus and the FDP stack co-installed in one environment and is a follow-up.
- **Error contract** — `FdpFetchError` is both a `FetchDataError` and an `mdsExceptions.MdsException`; a mocked fetch failure is caught by an `except mdsExceptions.MdsException` handler (the physics-fallback pattern). Real-env validation of the fallback firing is pending (see Fetch-error contract above).
- **Guard** — a node from the deferred PTDATA2 class raises a clear error (does not hang); the guard error is also `MdsException`-compatible.
- **Nickname** — verify `_efit_tree` resolves to `efit01` with `DummyDatabase` and to the DB-selected tree when a real d3drdb is available.

## Summary of decisions

| Decision | Choice |
|---|---|
| Scope | Full D3D parity (signals) |
| SQL layer | Out of scope (signals only) |
| Structure | Standalone `FDPDataConnection` + shared `TreeNicknameMixin`; duck-typed `retrieval_manager` checks |
| Mixin location | `disruption_py/inout/nickname.py` |
| Selection | Both — `connection_initializer` injection now + config branch for in-tree UX |
| Physics methods | Unchanged (translation layer) |
| Fetch-error contract | Dual-typed `FdpFetchError(FetchDataError, mdsExceptions.MdsException)` so physics `except mdsExceptions.MdsException` fallbacks fire unchanged (real-env validation pending) |
| Parity test | Closeness (tolerance), not exact equality (shipped test is sanity-level; full parity is a follow-up) |
| PTDATA2-backed nodes | Deferred to a separate follow-up spec; this spec only guards against them (fail loudly, never hang) |
