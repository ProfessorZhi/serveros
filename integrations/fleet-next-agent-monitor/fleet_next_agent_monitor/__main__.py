"""`python -m fleet_next_agent_monitor` — the one-shot tick, as a process."""
from __future__ import annotations

import sys

from .runtime import main

if __name__ == "__main__":
    sys.exit(main())
