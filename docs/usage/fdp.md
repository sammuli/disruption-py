# Reading DIII-D data over FDP

You can run disruption-py against the [Fusion Data Platform](https://github.com/GA-FDP)
instead of an on-site `atlas` MDSplus server — from anywhere, with no VPN and no
GA account beyond an FDP token.

**This needs no disruption-py code changes.** The FDP origin runs an mdsip relay
that speaks the ordinary MDSplus thin-client protocol, so the existing
`MDSConnection` works against it unmodified. You only change which server it
connects to.

## Setup

1. Install the FDP stack, which provides the `fdp://` MDSplus transport
   (`mdsip-fdp`) and the `fdp` CLI:

   ```bash
   pixi install          # see pixi.toml in the repo root
   ```

2. Mint a token (one-time; opens a browser):

   ```bash
   pixi run login        # == fdp login
   ```

## Point disruption-py at the origin

Add to `~/.config/disruption-py/user.toml`:

```toml
[d3d.inout.mds]
mdsplus_connection_string = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"
```

That's the whole change — it overrides the `"atlas"` default in
`disruption_py/machine/d3d/config.toml`. Remove the section to go back to atlas.

Then run as usual, inside an FDP-configured environment so `BEARER_TOKEN` is set:

```bash
fdp run python your_script.py
```

To set it per-script instead of globally, pass the connection string directly:

```python
from disruption_py.inout.mds import ProcessMDSConnection

FDP_D3D = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"

def fdp_connection():
    return ProcessMDSConnection(FDP_D3D)

get_shots_data(..., connection_initializer=fdp_connection)
```

`connection_initializer` must be picklable for `num_processes > 1`, so use a
module-level function rather than a lambda.

The URL is just the device's origin host and port under the `fdp://` scheme. If
you would rather not hard-code it, derive it from the FDP catalog:

```python
from fdp import catalog
url = "fdp://" + catalog["d3d"].schema.origin_server.split("://", 1)[-1] + "/mdsip"
```

## Why this works

`MDSConnection` is a thin client: it calls `conn.openTree(...)` and
`conn.get(<TDI expression>)`, and the server evaluates everything. That includes
the `ptdata('name', shot)` calls the D3D physics methods make — those are TDI
strings, not a separate data path. The FDP origin evaluates them, so PTDATA
pointnames, stored tree nodes, and PTDATA2/PTHEAD2-backed nodes (`\fs04`,
`\top.nb:pinj`, the raw bolometer channels) all resolve over one connection.

## Verification

Against atlas-equivalent output, on the full default D3D parameter set
(4 shots, 65 columns, `DummyDatabase`, `efit_nickname_setting="default"`):

| | serial | 4 processes |
|---|---|---|
| Runtime | 40.4 s | 9.3 s |
| Columns matching serial | — | 65/65 |

Multiprocess retrieval is safe: `num_processes > 1` reproduces serial output on
all 65 columns (the origin runs one process per connection, so tree context is
not shared between concurrent clients).

## Scope

- **Signal retrieval only.** The SQL layer (disruption times, shot lists,
  EFIT-tree selection) is unchanged — provide it as you do today, or use
  `DummyDatabase` if you are off-site and cannot reach d3drdb.
- All trees the D3D physics methods read (`efit*`, `d3d`, `bolom`, `rf`,
  `electrons`, `transport`, `efitrt1`) are served by the origin.
- DIII-D only. Other tokamaks keep their own `mdsplus_connection_string`.

## Examples

| Script | What it does |
|---|---|
| `examples/fdp_example.py` | One shot, a few parameters — the minimal case |
| `examples/fdp_disruption_dataset.py` | Full default parameter set over 20 shots (10 disrupted, 10 not) → NetCDF + CSV |
| `examples/fdp_disruption_plots.py` | Traces for one disrupted and one non-disrupted shot from that dataset |

```bash
pixi run example
pixi run dataset
pixi run plots
```
