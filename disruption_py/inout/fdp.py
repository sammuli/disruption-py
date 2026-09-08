#!/usr/bin/env python3

"""
FDP / Pelican signal-retrieval backend for disruption-py (DIII-D).

FDPDataConnection satisfies the DataConnection interface used by the D3D
physics methods, dispatching each get_data* call:
  * ptdata('name', shot) strings  -> toksearch_d3d.PtDataSignal
  * \\node + tree_name            -> toksearch.MdsSignal

MDSplus reads take one of two transports, selected by ``mds_location``:

  ``"auto"`` (default)
      An ``fdp://`` mdsip URL derived from the device's ``origin_server`` in the
      FDP catalog, e.g.
      ``fdp://fdp-d3d-origin.nationalresearchplatform.org:8443/mdsip``. The
      origin opens the tree and evaluates the node's stored TDI record, so only
      the result crosses the network rather than the whole tree file. Needs
      toksearch >= 2.9.0 and the mdsip-fdp package. Falls back to ``"pelican"``
      if the catalog does not name an origin.

  ``"pelican"``
      Tree *files* read over Pelican/XRootD (``location=None``), with TDI
      evaluated client-side. Correct, but much slower.

Both transports return identical values on every node these physics methods
read; ``"auto"`` is the default because it is far faster. Measured over 24
fetches spanning 3 shots and 5 trees, run in both orders to control for
caching: 0.58 s/fetch over ``fdp://`` vs 10.9-12.0 s/fetch over ``"pelican"``
(~19x), with the same 23 successes and the same single genuine NODATA.

Any other value is passed to ``MdsSignal(location=...)`` verbatim, so an
explicit ``fdp://...`` or ``remote://atlas.gat.com`` also works.

toksearch / toksearch_d3d are NOT dependencies of the disruption-py dev/test
environment, so their imports are guarded; unit tests patch the module globals
PtDataSignal / MdsSignal with mocks.
"""

import re
from typing import Any, List, Tuple
from urllib.parse import urlparse

import numpy as np
from loguru import logger

from disruption_py.config import config
from disruption_py.core.physics_method.errors import FetchDataError, NanDataError
from disruption_py.core.utils.misc import shot_msg
from disruption_py.core.utils.shared_instance import SharedInstance
from disruption_py.inout.base import DataConnection, ProcessConnection
from disruption_py.inout.mds import mdsExceptions
from disruption_py.inout.nickname import TreeNicknameMixin
from disruption_py.machine.tokamak import Tokamak

try:
    from toksearch import MdsSignal
except ModuleNotFoundError:
    # Guarded: the FDP signal stack may be absent (e.g. in CI). Real use runs
    # inside an FDP-configured env (`fdp run` / setup_environment()).
    MdsSignal = None

try:
    from toksearch_d3d import PtDataSignal
except ModuleNotFoundError:
    PtDataSignal = None


class FdpFetchError(FetchDataError, mdsExceptions.MdsException):
    """FDP fetch failure that is BOTH a disruption-py ``FetchDataError`` and an
    ``mdsExceptions.MdsException``.

    D3D physics methods catch ``mdsExceptions.MdsException`` to trigger
    per-signal fallbacks; raising this dual-typed error lets those fallbacks
    fire unchanged over FDP while the error remains a ``DataError``.
    """

    def __init__(self, message):
        # Bypass MdsException.__init__ (which may expect a status code) and use
        # the plain Exception initializer with our message.
        Exception.__init__(self, message)
        self.message = message

    def __str__(self):
        return self.message


# ptdata('name', shot) or ptdata("name", shot) -- captures the pointname.
_PTDATA_RE = re.compile(r"""^\s*ptdata\(\s*['"]([^'"]+)['"]\s*,.*\)\s*$""", re.IGNORECASE)

# Deny-list of MDSplus nodes that cannot be resolved on the "pelican" tree-file
# path, consulted only on that path. This was expected to be the
# PTDATA2/PTHEAD2-backed nodes, but they were measured to resolve over BOTH
# transports in a single process (\fs04, \top.nb:pinj and the raw bolometer
# channels all fetch fine over Pelican), so the list is empty and stays a guard
# hook rather than a workaround. See PTDATA2_HANDOFF.md for the history.
_PTDATA2_BACKED_NODES: set = set()


# Default path component of the mdsip relay on an FDP origin (xrdhttp-mdsip's
# `prefix=`). Only overridden by passing mds_location explicitly.
_MDSIP_RELAY_PREFIX = "mdsip"


def _derive_fdp_mds_location(device: str = "d3d"):
    """Return the ``fdp://`` mdsip URL for ``device``, or None if underivable.

    The FDP catalog names the origin as ``root://host:port``; the mdsip relay is
    the same host and port under the ``fdp://`` scheme, which MDSplus resolves to
    the mdsip-fdp transport. Returning None makes the caller fall back to reading
    tree files over Pelican.
    """
    try:
        from fdp import catalog

        origin = catalog[device].schema.origin_server
    except Exception as e:  # catalog absent, device unknown, schema changed
        logger.debug("FDP: could not derive an mdsip URL for {d}: {e}", d=device, e=e)
        return None
    if not origin:
        return None
    netloc = urlparse(origin).netloc or origin.split("://", 1)[-1]
    return f"fdp://{netloc}/{_MDSIP_RELAY_PREFIX}"


def _parse_ptdata_pointname(path: str):
    """Return the PTDATA pointname if ``path`` is a ptdata(...) expression, else None."""
    match = _PTDATA_RE.match(path)
    return match.group(1) if match else None


class ProcessFDPConnection(ProcessConnection):
    """
    Process-level FDP connection factory.

    Holds no heavy per-process state: PtDataSignal reuses a per-process
    PtDataReader internally (toksearch_d3d >= 0.10.0) and MdsSignal manages its
    own connections. Remaining ``options`` are reserved for future config knobs.

    Parameters
    ----------
    mds_location : str, optional
        Transport for MDSplus reads -- ``"auto"`` (default, an ``fdp://`` mdsip
        URL derived from the FDP catalog), ``"pelican"`` (tree files over
        Pelican), or a location string passed to ``MdsSignal`` verbatim. See the
        module docstring.
    device : str, optional
        FDP catalog device name used to derive the ``"auto"`` URL. Defaults to
        ``"d3d"``.
    """

    def __init__(self, mds_location: str = "auto", device: str = "d3d", **options: Any):
        self.device = device
        self.mds_location = self._resolve_mds_location(mds_location, device)
        self.options = options

    @staticmethod
    def _resolve_mds_location(mds_location: str, device: str):
        """Turn the ``mds_location`` setting into a concrete MdsSignal location."""
        if mds_location == "pelican":
            return None
        if mds_location != "auto":
            return mds_location
        derived = _derive_fdp_mds_location(device)
        if derived is None:
            logger.warning(
                "FDP: no origin in the catalog for {d}; MDSplus reads fall back to "
                "tree files over Pelican, where PTDATA2-backed nodes are unavailable.",
                d=device,
            )
        return derived

    @classmethod
    def from_config(cls, tokamak: Tokamak) -> "ProcessFDPConnection":
        """Create a shared per-process instance from the [<tokamak>.inout.fdp] config."""
        fdp_cfg = config(tokamak).inout.get("fdp", {}) or {}
        return SharedInstance(ProcessFDPConnection).get_instance(**dict(fdp_cfg))

    def get_shot_connection(self, shot_id: int) -> "FDPDataConnection":
        """Create a per-shot FDP data connection."""
        return FDPDataConnection(shot_id, mds_location=self.mds_location)


class FDPDataConnection(TreeNicknameMixin, DataConnection):
    """Per-shot FDP data connection dispatching to PtDataSignal / MdsSignal."""

    def __init__(self, shot_id: int, mds_location: str = None):
        self._shot_id = shot_id
        # None => MdsSignal reads tree files over Pelican; an fdp:// URL => the
        # origin's mdsip relay evaluates TDI server-side.
        self._mds_location = mds_location
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
            try:
                result = PtDataSignal(pointname).fetch(self._shot_id)
            except Exception as e:
                raise FdpFetchError(
                    f"FDP ptdata fetch failed for {pointname!r} "
                    f"(shot {self._shot_id}): {e}"
                ) from e
            return result, ("times",)

        # Only meaningful on the Pelican tree-file path, where TDI is evaluated
        # client-side. Over fdp:// the origin evaluates the record.
        if self._mds_location is None and path in _PTDATA2_BACKED_NODES:
            raise FdpFetchError(
                f"{path!r}: not resolvable over Pelican tree-file reads. Use the "
                'default mds_location="auto" (fdp:// mdsip) instead.'
            )

        resolved_tree = self.tree_name(tree_name)
        dim_names = self._ordered_dim_names(dim_nums)
        logger.trace(
            shot_msg("FDP mds fetch: {p} @ {t} via {loc}"),
            shot=self._shot_id,
            p=path,
            t=resolved_tree,
            loc=self._mds_location or "pelican tree files",
        )
        try:
            # NOTE: multi-dim (e.g. \psirz, dim_nums=[2]) forwards positional dim
            # names sized to the request. MdsSignal's data_order/shape handling for
            # >1-D nodes must be validated against real data (integration test); if
            # the axis order differs, thread a data_order kwarg through here.
            result = MdsSignal(
                path, resolved_tree, location=self._mds_location, dims=dim_names
            ).fetch(self._shot_id)
        except Exception as e:
            raise FdpFetchError(
                f"FDP mds fetch failed for {path!r} @ {resolved_tree!r} "
                f"(shot {self._shot_id}): {e}"
            ) from e
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
