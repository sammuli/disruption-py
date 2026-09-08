# disruption-py over the Fusion Data Platform

Read DIII-D data from anywhere. No VPN, and no on-site MDSplus or PTData
server: you point disruption-py's existing MDSplus connection at the FDP
origin instead.

**There are no code changes.** The origin runs an mdsip relay that speaks the
ordinary MDSplus thin-client protocol, so `MDSConnection` works against it
unmodified. All you change is the connection string:

```
atlas  ->  fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip
```

Everything in this branch is documentation, examples, a smoke test, and an
environment file. `disruption_py/` is untouched.

---

## 1. Install

```bash
git clone -b sammuli/fdp-origin-config https://github.com/sammuli/disruption-py.git
cd disruption-py

mamba env create -f environment.yml     # or: conda env create -f environment.yml
conda activate disruption-py-fdp
```

That is the minimal set: stock conda-forge MDSplus, one extra shared library
(`mdsip-fdp`, which installs `libMdsIpFDP.so` and teaches MDSplus the `fdp://`
scheme), and disruption-py via pip. There is no `toksearch`, no `ptdata`, no
XRootD and no Pelican client, because the origin evaluates everything
server-side.

## 2. Authenticate

This assumes someone has given you a token file.

```bash
mkdir -p ~/.fdp && chmod 700 ~/.fdp
cp /path/to/the/token ~/.fdp/token && chmod 600 ~/.fdp/token
export BEARER_TOKEN=$(cat ~/.fdp/token)
```

Tokens last about a month. The transport will read `~/.fdp/token` on its own if
`BEARER_TOKEN` is unset, but exporting it is explicit and is what you want in a
job script. To mint your own you need the `pelican` client, which
`docs/usage/fdp.md` covers.

## 3. Point disruption-py at the origin

```bash
mkdir -p ~/.config/disruption-py
cat >> ~/.config/disruption-py/user.toml <<'TOML'
[d3d.inout.mds]
mdsplus_connection_string = "fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip"
TOML
```

This overrides the `"atlas"` default in `disruption_py/machine/d3d/config.toml`.
Delete the section to go back to atlas. To set it per script instead of
globally, see `docs/usage/fdp.md`.

---

## Kicking the tires

### Check the origin is reachable

```bash
pytest tests/test_fdp_origin.py -v
```

You should get 3 passed. The three tests cover a stored EFIT node, a
`ptdata('ip', shot)` TDI call evaluated server-side, and `\fs04`, whose stored
record calls `PTDATA2`. They skip silently if `BEARER_TOKEN` is unset.

### One shot, a few parameters

```bash
python examples/fdp_example.py
```

```
Shot 161228: peak |Ip| = 1.036 MA (fetched from the FDP origin)
```

If you are off-site, the default path will not work. It uses the GA d3drdb SQL
server for disruption times and EFIT-tree selection. Add `--no-sql`, which pins
EFIT to `efit01` and uses a fixed timebase:

```bash
python examples/fdp_example.py --no-sql          # peak |Ip| = 0.990 MA
python examples/fdp_example.py --shot 194000 --no-sql
```

The two numbers differ because the timebases differ, not because the data does.

### A real dataset

The full default parameter set (every D3D physics method) over 20 shots,
10 disrupted and 10 not, written as NetCDF and CSV:

```bash
python examples/fdp_disruption_dataset.py                    # ~5 min on 8 procs
python examples/fdp_disruption_dataset.py --no-sql --shots 194000 194005 --num-processes 2
```

Output lands in `./fdp_output/`. Note that `--no-sql` drops the disruption
timebase, so `time_until_disrupt` will be empty.

Then plot one disrupted and one non-disrupted shot side by side. This needs
`mamba install matplotlib`:

```bash
python examples/fdp_disruption_plots.py          # writes fdp_output/example_shots.png
```

The script picks the disrupted shot from `time_until_disrupt`, so a dataset
built with `--no-sql` gives you only the non-disrupted panel. Pass
`--disrupted` and `--non-disrupted` to choose shots yourself.

### Confirm the data is coming from FDP

```bash
python -c "
from disruption_py.inout.mds import ProcessMDSConnection
c = ProcessMDSConnection('fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip').conn
c.openTree('efit01', 161228)
print('betan:', c.get(r'\efit_a_eqdsk:betan').data()[:5])
print('ip   :', c.get(\"ptdata('ip',161228)\").data()[:5])
"
```

Both values come back over the same connection. The second one is a TDI
expression that the origin evaluates for you, which is why PTDATA needs no
separate data path.

---

## Troubleshooting

**A connection error that mentions ssh.** MDSplus silently falls back to the
ssh-tunnel transport when it cannot load a protocol, so a missing
`libMdsIpFDP.so` looks like a connection failure rather than a missing
protocol. Run `pytest tests/test_fdp_origin.py`, which checks resolution
directly.

**`MdsIpException: Error connecting to fdp://...`** usually means no token, or
an expired one. Check `echo $BEARER_TOKEN | cut -c1-20` and the age of
`~/.fdp/token`.

**`Can't pickle <function <lambda>>`** happens because
`connection_initializer` is pickled to the worker processes when
`num_processes > 1`. Use a module-level function, as the examples do.

**SQL connection failures on the default path.** That path needs the GA
network, and FreeTDS needs `export TDSVER=7.0`, which disruption-py does not
set for you. Use `--no-sql` if you are off-site.

---

## More

`docs/usage/fdp.md` covers the transport details, the measured performance
(40.4 s serial and 9.3 s on 4 processes for 4 shots across 65 columns, with
multiprocess output identical to serial), and what the environment actually
requires.
