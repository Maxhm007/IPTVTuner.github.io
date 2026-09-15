#!/usr/bin/env python3
import sys

import iptv_self_heal as core

_original_is_restricted = core.is_restricted
_approved = core.load_approved_free()


def stable_is_restricted(entry):
    """Keep premium channels restricted except while actively using an approved free URL."""
    if not _original_is_restricted(entry):
        return False
    approved_url = core.approved_free_url(entry, _approved)
    if (
        not entry.get("inactive")
        and approved_url
        and entry.get("url") == approved_url
    ):
        return False
    return True


core.is_restricted = stable_is_restricted


if __name__ == "__main__":
    core.heal(
        sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u",
        sys.argv[2] if len(sys.argv) > 2 else None,
    )
