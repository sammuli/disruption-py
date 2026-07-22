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
