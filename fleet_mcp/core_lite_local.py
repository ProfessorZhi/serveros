"""This surface's own device: Core-Lite read from the file its Core publishes.

Kept out of `core_lite_source`, whose rule is that it opens nothing: the seam
stays a pure admission function, and the one reader that touches the local
filesystem lives here, using the node connector's bounded, no-follow,
owner-only read.
"""
from __future__ import annotations

import json
import os
import stat

from .core_lite_source import CoreLiteFetch, CoreLiteRoute
from .errors import FleetRefusal
from .node.connector import CORE_SNAPSHOT_MAX_BYTES, _read_bounded_no_follow


class LocalFileCoreLiteSource:
    """This surface's OWN device, read from the status file its Core publishes.

    The route is the local filesystem: the file must be a regular, owner-only
    file owned by this account (the same bounded, no-follow read the node
    connector uses), so `authenticated=True` means "this account's own Core
    wrote it", never "the document says so". It answers only for the device
    this surface runs on; asked about any other device it names its own device
    in the route, so `admit_core_lite` refuses the mismatch.
    """

    def __init__(self, path, local_device_ref: str) -> None:
        self.path = path
        self.local_device_ref = local_device_ref

    def fetch(self, device_ref: str) -> CoreLiteFetch:
        route = CoreLiteRoute(device_ref=self.local_device_ref, authenticated=True)
        try:
            # Owner-only, as publish-status.sh writes it: a file others can read
            # (or write) is not this account's private reading.
            if stat.S_IMODE(os.lstat(self.path).st_mode) & 0o077:
                raise FleetRefusal("BAD_REQUEST", "the Core-Lite status file is not owner-only")
            raw = _read_bounded_no_follow(self.path, CORE_SNAPSHOT_MAX_BYTES)
            document = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return CoreLiteFetch(document=None, route=route)
        except (FleetRefusal, OSError, UnicodeDecodeError, ValueError):
            # Present but not trustworthy (wrong owner, widened mode, symlink,
            # too large, not JSON): no document, and the route is not claimed.
            return CoreLiteFetch(document=None,
                                 route=CoreLiteRoute(device_ref=self.local_device_ref,
                                                     authenticated=False))
        return CoreLiteFetch(document=document, route=route)
