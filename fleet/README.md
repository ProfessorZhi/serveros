# ServerOS Fleet

ServerOS owns **device identity, environment role and connection topology** because these answer Environment questions: where am I, what machine is this, and how may another authorized agent reach it?

Ability Management does not duplicate this registry. It references `device_ref` and owns only capability desired/observed state for that device.

## Authority split

```text
serveros/fleet device registry        stable identity / role / public environment facts
~/.serveros/targets.yaml              local-only connection locator / credential references
state/serveros/fleet/<device>/        dynamic reachability / heartbeat / observations
ability-management fleet policy       desired capability packages/profiles
state/serveros/ability-management/    current ability materialization snapshot
```

No hostname, IP, SSH user/key path, token, machine-id, MAC, asset tag or hardware serial belongs in the public device registry.

## Enrollment

A discovered endpoint is not a device identity until explicitly enrolled or matched by cryptographic/stable evidence. Unknown mapping stays `UNRESOLVED`; never merge two device identities because their installed Skills look similar.

The current migration therefore does **not** assume `FJ-NJ-CL-250028 == dell-work` without an enrollment proof.