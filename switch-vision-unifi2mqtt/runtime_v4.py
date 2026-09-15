#!/usr/bin/env python3
from __future__ import annotations

import multi_controller
import unifi2mqtt as core
from port_activity import VERSION, install_runtime_hooks, poll_once_v4


def install() -> None:
    """Activate the 4.0 traffic contract for every controller poll path."""
    install_runtime_hooks()
    core.VERSION = VERSION
    multi_controller.VERSION = VERSION

    # poll_multi_once has a positional default poller; priority/fallback uses a
    # keyword-only default. Replace both without altering the established
    # multi-controller state/retirement machinery.
    defaults = multi_controller.poll_multi_once.__defaults__ or ()
    if len(defaults) != 1:
        raise RuntimeError("Unexpected poll_multi_once default contract")
    multi_controller.poll_multi_once.__defaults__ = (poll_once_v4,)

    kwdefaults = dict(multi_controller.poll_single_with_failover.__kwdefaults__ or {})
    if "poller" not in kwdefaults:
        raise RuntimeError("Unexpected poll_single_with_failover default contract")
    kwdefaults["poller"] = poll_once_v4
    multi_controller.poll_single_with_failover.__kwdefaults__ = kwdefaults


if __name__ == "__main__":
    install()
    raise SystemExit(multi_controller.main())
