# Reading DIII-D data over FDP

You can run disruption-py against the [Fusion Data Platform](https://github.com/GA-FDP)
instead of an on-site `atlas` MDSplus server — from anywhere, with no VPN and no
GA account beyond an FDP token.

**This needs no disruption-py code changes.** The FDP origin runs an mdsip relay
that speaks the ordinary MDSplus thin-client protocol, so the existing
`MDSConnection` works against it unmodified. You only change which server it
connects to.

## Setup

You need exactly two things: the `fdp://` MDSplus transport, and a token.

### 1. Create the environment

```bash
mamba env create -f environment.yml     # or: conda env create -f ...
conda activate disruption-py-fdp
```

That is the minimal set — 55 packages. It installs stock conda-forge MDSplus
plus one extra shared library (`mdsip-fdp` → `libMdsIpFDP.so`), `pelican` for
authentication, and disruption-py via pip. It deliberately does **not** install
the FDP data-access stack: no `toksearch`, no `toksearch_d3d`, no `ptdata`, no
XRootD, no Pelican client library. None of it is needed, because the origin
evaluates everything server-side.

MDSplus loads the transport by name — it uppercases a URL's scheme, builds
`"MdsIp" + SCHEME`, and `dlopen`s the result from beside `libMdsShr.so`. The
conda package puts it there for you.

### 2. Authenticate

The transport needs a bearer token. If someone has given you one — the usual
case for a collaborator — put it at `~/.fdp/token` and export it:

```bash
mkdir -p ~/.fdp && chmod 700 ~/.fdp
cp /path/to/the/token ~/.fdp/token && chmod 600 ~/.fdp/token
export BEARER_TOKEN=$(cat ~/.fdp/token)
```

The transport also reads `~/.fdp/token` on its own if `BEARER_TOKEN` is unset,
so the export is belt-and-braces — but it makes the dependency explicit, and it
is what you want in a job script. Tokens are typically good for about a month.

To mint one yourself instead, **in a real terminal** (pelican refuses a new
consent flow when its stdout is not a TTY):

```bash
pelican credentials token get read pelican://osg-htc.org:443/fdp-d3d
```

Add `--json` for a JSON object and read its `access_token` key.

> If you are inside the GA FDP development environment instead, `pixi.toml` in
> the repo root pulls the full stack and wraps both steps: `pixi install`, then
> `pixi run login`.

## Point disruption-py at the origin

Add to `~/.config/disruption-py/user.toml`:

```toml
[d3d.inout.mds]
mdsplus_connection_string = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"
```

That's the whole change — it overrides the `"atlas"` default in
`disruption_py/machine/d3d/config.toml`. Remove the section to go back to atlas.

Then run as usual:

```bash
fdp run python your_script.py
```

### What the environment actually needs

`fdp run` sets 21 variables, but signal retrieval needs only **a token** —
either the `BEARER_TOKEN` environment variable or a `~/.fdp/token` file (`fdp
login` writes the latter). Nothing else is required: no `XRD_PLUGINCONFDIR`, no
`PTDATA_*`, no `default_tree_path`, no `MDS_PATH`. Nothing touches Pelican or
reads a tree file locally, so there is nothing to configure — the origin does
all of it.

That means you can skip `fdp run` entirely and embed this in an environment of
your own:

```bash
BEARER_TOKEN="$(cat ~/.fdp/token)" python your_script.py
```

(`~/.fdp/token` is written by `fdp login`. Without the `fdp` package — the
standalone setup above — export `BEARER_TOKEN` from pelican's output directly,
or write the JWT to `~/.fdp/token` yourself; the transport reads either.)

Verified on the full default parameter set (2 shots, 2 processes, 65 columns)
with every other FDP variable unset: works with the env var alone, works with
only the `~/.fdp/token` file, and fails with `MdsIpException` when neither is
present.

One exception: if you use the **default d3drdb** connection rather than
`DummyDatabase`, that path still needs `TDSVER` (FreeTDS requires a protocol
version and `disruption_py/inout/sql.py` does not set one). `fdp run` sets it
for you; otherwise export `TDSVER=7.0`.

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
