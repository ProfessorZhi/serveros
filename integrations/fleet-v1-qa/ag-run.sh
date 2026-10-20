#!/bin/bash
# Q1 snapshot AG — independent re-run of the QA2 S1/R1 negatives.
# Usage: ag-run.sh <qa2-harness-dir> [workdir]
# The harness is the read-only QA2 directory; this wrapper does not copy its
# probe source into this repository. It exports the exact PR heads itself.
set -u
QA2="${1:?path to integrations/fleet-v1-qa/qa2 required}"
WORK="${2:-/tmp/qa2-independent-security}"
test -x "$QA2/qa2-run.sh" || { echo "missing $QA2/qa2-run.sh" >&2; exit 2; }
echo "== pinned SHAs in the harness"
grep -E '^(S1|R1)_SHA=' "$QA2/qa2-run.sh"
echo "== run"
QA2_WORKDIR="$WORK" timeout 600 "$QA2/qa2-run.sh" all
rc=$?
echo "QA2_ALL_RC=$rc (findings rc=1 is expected RED)"
exit 0
