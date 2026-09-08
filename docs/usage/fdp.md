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
    shotlist_setting=[161228],
    connection_initializer=ProcessFDPConnection,  # called with no args -> ProcessFDPConnection()
    # ... your other settings ...
)
```

`connection_initializer` is invoked with no arguments, so pass the
`ProcessFDPConnection` **class** (not `ProcessFDPConnection.from_config`, which
needs a `tokamak`). See `examples/fdp_example.py` for a complete, runnable script.

Run it: `fdp run python your_script.py`

## Option B — select FDP via config

Add to `~/.config/disruption-py/user.toml`:

```toml
[d3d.inout.fdp]
```

With that section present, `get_process_connection` selects the FDP backend for
D3D. Remove it to fall back to the default `atlas` MDSplus connection.

## How MDSplus reads are transported

`ProcessFDPConnection` takes an `mds_location` option:

| Value | Transport | TDI evaluated |
|---|---|---|
| `"auto"` (default) | `fdp://` mdsip URL derived from the device's `origin_server` in the FDP catalog | server-side, on the origin |
| `"pelican"` | tree *files* over Pelican/XRootD | client-side, in your process |
| anything else | passed to `MdsSignal(location=...)` verbatim (e.g. `remote://atlas.gat.com`) | — |

The default resolves to
`fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip` for DIII-D. It
needs `toksearch >= 2.9.0` and the `mdsip-fdp` package, both of which come with
`fdp-core`. If the catalog names no origin, the backend logs a warning and falls
back to `"pelican"`.

### Why `fdp://` is the default: speed, not capability

Both transports return **identical values** on every node the D3D physics
methods read. `fdp://` is the default because it is far faster: the origin opens
the tree and evaluates the node's record, so only the result crosses the
network, where the `"pelican"` path transfers whole tree files and evaluates TDI
client-side.

Measured over 24 fetches spanning 3 shots and 5 trees (`efit01`, `d3d`, `bolom`,
`rf`, `transport`), run in both orders to control for caching:

| Transport | Total | Per fetch | Result |
|---|---|---|---|
| `fdp://` | 14.0 s | 0.58 s | 23 ok, 1 genuine NODATA |
| `"pelican"` | 261–288 s | 10.9–12.0 s | 23 ok, same NODATA |

Note this corrects an earlier expectation recorded in `PTDATA2_HANDOFF.md`:
PTDATA2/PTHEAD2-backed nodes (`\fs04`, `\top.nb:pinj`, the raw bolometer
channels) were expected to be unreachable on the Pelican path, but they fetch
correctly there in a single process. `_PTDATA2_BACKED_NODES` is therefore empty
and remains only as a guard hook.

```python
# Force the old tree-file transport:
connection_initializer=lambda: ProcessFDPConnection(mds_location="pelican"),
```

## Scope

- PTDATA pointnames go to `PtDataSignal` over the Pelican JSON index; MDSplus
  tree nodes go to `MdsSignal` over the transport above. All seven trees the D3D
  physics methods read (`efit*`, `d3d`, `bolom`, `rf`, `electrons`, `transport`,
  `efitrt1`) are served.
- The SQL layer (disruption times, shot lists, EFIT-tree selection) is unchanged
  — provide it as you do today, or use `DummyDatabase`.
- Multiprocess retrieval is safe: `num_processes > 1` was verified to produce
  output identical to serial across all 66 columns of a 4-shot run (the origin's
  mdsip relay now runs one process per connection, so tree context is not
  shared between concurrent clients).

## Example: building a disruption dataset

`examples/fdp_disruption_dataset.py` runs the full default D3D parameter set
over 20 shots (10 disrupted, 10 not) and writes NetCDF + CSV:

```bash
pixi run dataset
```
