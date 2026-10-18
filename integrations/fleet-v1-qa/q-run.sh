#!/bin/bash
set +e
QA_MODE=old QA_TREE="$1/p-v3" WE_TREE="$1/m-we15-tree" QA_SCRATCH="$1/q-scratch" \
  python3 "$1/qa_probe_q_277.py"
old=$?
QA_MODE=new QA_TREE="$1/q-277" WE_TREE="$1/m-we15-tree" QA_SCRATCH="$1/q-scratch" \
  python3 "$1/qa_probe_q_277.py"
new=$?
echo "old_exit=$old new_exit=$new"
if [ "$old" -eq 0 ] && [ "$new" -eq 0 ]; then echo CONTROL_OK; else exit 1; fi
