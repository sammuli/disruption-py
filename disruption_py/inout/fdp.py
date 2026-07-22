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
except ModuleNotFoundError:
    # Guarded: the FDP signal stack may be absent (e.g. in CI). Real use runs
    # inside an FDP-configured env (`fdp run` / setup_environment()).
    MdsSignal = None

try:
    from toksearch_d3d import PtDataSignal
except ModuleNotFoundError:
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
                raise FetchDataError(
                    f"FDP ptdata fetch failed for {pointname!r} "
                    f"(shot {self._shot_id}): {e}"
                ) from e
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
        try:
            result = MdsSignal(
                path, resolved_tree, location=None, dims=dim_names
            ).fetch(self._shot_id)
        except Exception as e:
            raise FetchDataError(
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
