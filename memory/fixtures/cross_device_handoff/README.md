# Cross-device handoff fixture — §59

Three records and a read. This directory is a **fixture**, not ledger content:
nothing here describes real work, and no real ledger should ever contain it.
The three records say so by carrying this path in their `source` field, and
`tests/test_cross_device_handoff.py` asserts that every record in the directory
does — a fixture that cannot be told from a record is one that gets cited.

```text
step 1  Beijing   checkpoint  mem:fixture-beijing-checkpoint-1
        Beijing   handoff     mem:fixture-beijing-handoff-1
step 2  work-fj   reads the handoff it is addressed by     handoff.pending()
step 3  work-fj   result      mem:fixture-work-fj-result-1
step 4  Beijing   retrieves the result                     handoff.answers()
```

Step 4 writes nothing. That is the property being demonstrated rather than a
gap in the fixture: **the sender does not wait.** Beijing starts the round trip,
goes away, and later asks the ledger whether its handoff was answered. It never
contacts the other device, so the two never have to be awake at the same time —
which is the only reason an async handoff is worth having over an RPC.

The fixture is deliberately run against the ledger *files*, not against a live
device: §59's fallback for an unavailable remote actuator is the Git ledger
itself. See `memory/handoff.py`.
