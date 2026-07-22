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
