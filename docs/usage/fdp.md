# Reading DIII-D data over FDP

You can run disruption-py against the [Fusion Data Platform](https://github.com/GA-FDP)
instead of an on-site `atlas` MDSplus server, from anywhere, with no VPN and no
GA account beyond an FDP token.

**This needs no disruption-py code changes.** The FDP origin runs an mdsip relay
that speaks the ordinary MDSplus thin-client protocol, so the existing
`MDSConnection` works against it unmodified. You only change which server it
connects to.

## Setup

You need two things: the `fdp://` MDSplus transport, and a token.

### 1. Create the environment

```bash
mamba env create -f environment.yml     # or: conda env create -f ...
conda activate disruption-py-fdp
```

That is the minimal set. It installs stock conda-forge MDSplus, one extra
shared library (`mdsip-fdp`, which provides `libMdsIpFDP.so`), and
disruption-py via pip. It deliberately leaves out the FDP data-access stack:
no `toksearch`, no `toksearch_d3d`, no `ptdata`, no XRootD, no Pelican client
library. None of that is needed, because the origin evaluates everything
server-side.

MDSplus finds the transport by name. It uppercases a URL's scheme, builds
`"MdsIp" + SCHEME`, and `dlopen`s the result from beside `libMdsShr.so`. The
conda package puts it there for you.

### 2. Authenticate

The transport needs a bearer token, and this environment assumes you have been
given one. Put it at `~/.fdp/token` and export it:

```bash
mkdir -p ~/.fdp && chmod 700 ~/.fdp
cp /path/to/the/token ~/.fdp/token && chmod 600 ~/.fdp/token
export BEARER_TOKEN=$(cat ~/.fdp/token)
```

The transport also reads `~/.fdp/token` on its own when `BEARER_TOKEN` is
unset, so the export is belt and braces. It does make the dependency explicit,
though, and it is what you want in a job script. Tokens are typically good for
about a month.

Minting your own needs the `pelican` client, which is deliberately not part of
this environment. Either ask whoever supplied the token for a fresh one, or
grab a standalone binary (`pelican_Linux_x86_64.tar.gz`) from the
[Pelican releases](https://github.com/PelicanPlatform/pelican/releases) and run
it **in a real terminal**, since it refuses a new consent flow when stdout is
not a TTY:

```bash
pelican credentials token get read pelican://osg-htc.org:443/fdp-d3d
```

> Inside the GA FDP development environment you can use `pixi.toml` in the repo
> root instead. It pulls the full stack and wraps both steps: `pixi install`,
> then `pixi run login`.

## Point disruption-py at the origin

Add to `~/.config/disruption-py/user.toml`:

```toml
[d3d.inout.mds]
mdsplus_connection_string = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"
```

That is the whole change. It overrides the `"atlas"` default in
`disruption_py/machine/d3d/config.toml`. Remove the section to go back to
atlas. Then run your script as usual:

```bash
python your_script.py
```

To set it per script instead of globally, pass the connection string directly:

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
you have the `fdp` package installed and would rather not hard-code it, derive
it from the catalog:

```python
from fdp import catalog
url = "fdp://" + catalog["d3d"].schema.origin_server.split("://", 1)[-1] + "/mdsip"
```

## What the environment actually needs

Signal retrieval needs only a token, from either the `BEARER_TOKEN` environment
variable or a `~/.fdp/token` file. Nothing else is required: no
`XRD_PLUGINCONFDIR`, no `PTDATA_*`, no `default_tree_path`, no `MDS_PATH`.
Nothing touches Pelican or reads a tree file locally, so there is nothing to
configure. The origin does all of it.

This matters if you work in the GA environment, where `fdp run` sets 21
variables. You do not need it here:

```bash
BEARER_TOKEN="$(cat ~/.fdp/token)" python your_script.py
```

Verified on the full default parameter set (2 shots, 2 processes, 65 columns)
with every other FDP variable unset. It works with the environment variable
alone, works with only the `~/.fdp/token` file, and fails with `MdsIpException`
when neither is present.

There is one exception. If you use the default d3drdb connection rather than
`DummyDatabase`, that path still needs `TDSVER`, because FreeTDS requires a
protocol version and `disruption_py/inout/sql.py` does not set one. Export
`TDSVER=7.0`, or use `fdp run`, which sets it for you.

## Why this works

`MDSConnection` is a thin client. It calls `conn.openTree(...)` and
`conn.get(<TDI expression>)`, and the server evaluates everything. That
includes the `ptdata('name', shot)` calls the D3D physics methods make, which
are TDI strings rather than a separate data path. The FDP origin evaluates
them, so PTDATA pointnames, stored tree nodes, and PTDATA2/PTHEAD2-backed nodes
(`\fs04`, `\top.nb:pinj`, the raw bolometer channels) all resolve over one
connection.

## Verification

Against atlas-equivalent output, on the full default D3D parameter set
(4 shots, 65 columns, `DummyDatabase`, `efit_nickname_setting="default"`):

| | serial | 4 processes |
|---|---|---|
| Runtime | 40.4 s | 9.3 s |
| Columns matching serial | n/a | 65/65 |

Multiprocess retrieval is safe. `num_processes > 1` reproduces serial output on
all 65 columns, because the origin runs one process per connection, so tree
context is not shared between concurrent clients.

## Scope

- **Signal retrieval only.** The SQL layer (disruption times, shot lists,
  EFIT-tree selection) is unchanged. Provide it as you do today, or use
  `DummyDatabase` if you are off-site and cannot reach d3drdb.
- All trees the D3D physics methods read (`efit*`, `d3d`, `bolom`, `rf`,
  `electrons`, `transport`, `efitrt1`) are served by the origin.
- DIII-D only. Other tokamaks keep their own `mdsplus_connection_string`.

## Examples

| Script | What it does |
|---|---|
| `examples/fdp_example.py` | One shot, a few parameters. The minimal case. |
| `examples/fdp_disruption_dataset.py` | Full default parameter set over 20 shots (10 disrupted, 10 not), written as NetCDF and CSV |
| `examples/fdp_disruption_plots.py` | Traces for one disrupted and one non-disrupted shot from that dataset |

```bash
python examples/fdp_example.py
python examples/fdp_disruption_dataset.py
python examples/fdp_disruption_plots.py
```

The first two take `--no-sql`, which skips d3drdb by pinning EFIT to `efit01`
and using a fixed timebase. You need it off-site. The plotting script only
reads a dataset the second one wrote, so it takes `--indir`, `--disrupted` and
`--non-disrupted` instead. See `README_FDP.md` for a fuller walkthrough.
