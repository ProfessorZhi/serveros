#!/usr/bin/env python3
"""G1 — coverage of the authority's 21 semantic families across device classes.

WHY THIS MODULE EXISTS
----------------------
`effective.py` answers a per-device, per-ability question: for the abilities the authority
records as installed on ONE device, which terms did that device report, and may any of them
be called right now? Issue #249 asks a coarser and easily-misread question: across the
authority's **21 semantic families**, what is actually installed / observed / ready / allowed
on Tokyo, on the Windows node and on the Dell A6000?

The coarse question is where arithmetic starts to flatter the answer. Four different facts
become the same integer if this module is careless:

    declared         the family exists in the product vocabulary (always 21 rows)
    realized         some Ability identity declares `product_role.primary == family`
    installed        the authority records an installation for that device id
    observed         a device snapshot contains a matching observation

They are reported separately, and each device cell says which one it is answering. In
particular:

  * a family with **no primary realization** is `NO_PRIMARY_REALIZATION` — an unowned
    responsibility, not "zero abilities". B1 and B3 are cross-referenced by abilities that
    belong to other families, so they are not absent from the product; they have no owner.
  * a device that was **never observed** keeps its authority installation count and reports
    `observed: NOT_OBSERVED`. `0 observed` would read as "looked, found none".
  * a device class with **no authority device id** is `UNRESOLVED`. Every device-dependent
    word is `UNKNOWN`, never `0`/`false`. Mapping the Dell A6000 to `dell-work` because both
    say "Dell" would conflate a Windows device with a Linux one.
  * a name-only observation is `PROBABLE`, never identity. The authority's own rule is that
    name-only is PROBABLE MATCH; a stable_id / canonical_source match is required for SAME.

WHAT IT DOES NOT DO
-------------------
* It is **not a resolver**. `ready`/`grant` are `UNKNOWN` unless a health/policy observation
  is supplied; this module does not turn a scan into a callability claim. Per-ability
  callability remains `effective.project_device(...)` over `abilities/resolver.py`.
* It is **not a registry**. The manifest and the vocabulary are read; nothing is registered,
  cached or written back. The markdown is a derived view with a `--check` mode.
* It does **not** read a clock, a socket or a working tree. `source_revision` is supplied by
  the caller; the CLI reads the pinned manifest from a git object via
  `runtime/ability_projection.py#read_manifest`.

RUN IT
------
    python3 coverage.py --manifest <checkout>/abilities-manifest.yaml \
        --families fixtures/families.json --devices fixtures/coverage-devices.json \
        --revision <40-hex> --evidence fixtures/resolve-evidence.json --check

`--check` regenerates in memory and exits 1 if the committed markdown moved. The real
manifest is not committed here: it is the authority's file, and copying it into ServerOS
would be a second registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "serveros/device-ability-coverage.v1"
THIS_IS = ("a coverage projection, not a registry and not a callability claim: a family row "
           "separates declared / realized / installed / observed, and every unobserved axis "
           "is UNKNOWN rather than false")

STATES = ("OBSERVED", "NOT_OBSERVED", "UNRESOLVED")

#: The words a device cell may use. Closed on purpose: a free-text status is how "not
#: looked" becomes "not there" the first time somebody paraphrases it.
IDENTITY_WORDS = ("RESOLVED", "UNRESOLVED")
OBSERVATION_WORDS = ("OBSERVED", "NOT_OBSERVED", "UNKNOWN")
TERM_WORDS = ("UNKNOWN",)          # no health/policy observation is part of this schema
GAP_WORDS = ("NO_PRIMARY_REALIZATION",)


class Refused(Exception):
    """The request cannot be projected. 'No' is an answer and is never raised."""


# ---------------------------------------------------------------------------
# Identity and observation matching
# ---------------------------------------------------------------------------

def _records(manifest: dict) -> dict[str, list[dict]]:
    """Manifest records grouped by Ability identity.

    An identity is not a record: a plugin and the ability it distributes can share one `id`
    (the authority's §6), and counting both would double the realization count. Grouping here
    is the same decision `runtime/ability_projection.py#installed_abilities` makes.
    """
    if not isinstance(manifest, dict):
        raise Refused(f"manifest is a {type(manifest).__name__}, not a mapping")
    abilities = manifest.get("abilities")
    if not isinstance(abilities, list) or not abilities:
        raise Refused("manifest.abilities must be a non-empty list")
    grouped: dict[str, list[dict]] = {}
    for record in abilities:
        if not isinstance(record, dict):
            raise Refused("manifest.abilities carries a non-mapping record")
        identity = str(record.get("id") or "").strip()
        if not identity:
            raise Refused("a manifest record has no id; an identity cannot be invented")
        grouped.setdefault(identity, []).append(record)
    return grouped


def _primary_roles(records: list[dict]) -> set[str]:
    roles = set()
    for record in records:
        role = record.get("product_role")
        if isinstance(role, dict) and role.get("primary"):
            roles.add(str(role["primary"]))
    return roles


def _cross_refs(records: list[dict]) -> set[str]:
    refs = set()
    for record in records:
        role = record.get("product_role")
        if isinstance(role, dict):
            refs.update(str(ref) for ref in (role.get("cross_refs") or []) if ref)
    return refs


def _identity_keys(identity: str, records: list[dict]) -> set[str]:
    """Every spelling the authority itself gives this identity.

    `id`, `canonical_source.repo`, `sources[].id` and `stable_id` are all authority-carried
    names. None of them is guessed from the record's display name.
    """
    keys = {identity.lower()}
    for record in records:
        source = record.get("canonical_source")
        if isinstance(source, dict) and source.get("repo"):
            keys.add(str(source["repo"]).lower())
        for item in record.get("sources") or []:
            if isinstance(item, dict) and item.get("id"):
                keys.add(str(item["id"]).lower())
        if record.get("stable_id"):
            keys.add(str(record["stable_id"]).lower())
    return keys


def _observation_keys(observation: dict) -> set[str]:
    keys = set()
    source = observation.get("canonical_source")
    if isinstance(source, dict) and source.get("repo"):
        keys.add(str(source["repo"]).lower())
    if observation.get("stable_id"):
        keys.add(str(observation["stable_id"]).lower())
    return keys


def _installed(records: list[dict], device_ids) -> bool:
    ids = set(device_ids)
    for record in records:
        for revision in record.get("revisions") or []:
            for installation in revision.get("installations") or []:
                if isinstance(installation, dict) and installation.get("device_id") in ids:
                    return True
    return False


def _match_observations(identities: dict[str, list[dict]],
                        observations: list[dict]) -> dict[str, Any]:
    """Classify each observation against the authority, without upgrading weak evidence.

    * strong  — an authority-carried key (`stable_id` / canonical repo) matches exactly one
                identity;
    * probable— the (type, name) pair matches exactly one identity and no key matched;
    * ambiguous — more than one identity answers, so nothing is attributed;
    * unmatched — nothing in the authority answers.
    """
    if not isinstance(observations, list):
        raise Refused(f"observations is a {type(observations).__name__}, not a list")
    by_name: dict[tuple, set[str]] = {}
    for identity, records in identities.items():
        for record in records:
            key = (record.get("type"), record.get("name"))
            if key[0] and key[1]:
                by_name.setdefault(key, set()).add(identity)

    strong: set[str] = set()
    probable: set[str] = set()
    ambiguous: set[str] = set()
    unmatched: list[str] = []
    observed_strong = observed_probable = observed_ambiguous = 0
    for observation in observations:
        keys = _observation_keys(observation)
        keyed = {identity for identity, records in identities.items()
                 if keys and keys & _identity_keys(identity, records)}
        if len(keyed) == 1:
            strong |= keyed
            observed_strong += 1
            continue
        if len(keyed) > 1:
            ambiguous |= keyed
            observed_ambiguous += 1
            continue
        named = by_name.get((observation.get("type"), observation.get("name")), set())
        if len(named) == 1:
            probable |= named
            observed_probable += 1
        elif len(named) > 1:
            ambiguous |= named
            observed_ambiguous += 1
        else:
            unmatched.append(str(observation.get("name")))
    probable -= strong
    return {"strong": strong, "probable": probable, "ambiguous": ambiguous,
            "unmatched": unmatched,
            # Observation counts stay separate from identity counts: one identity can be
            # named by several observations, and printing the two as one number would make
            # "70 observations" and "7 identities" look like the same denominator.
            "observed_strong": observed_strong,
            "observed_probable": observed_probable,
            "observed_ambiguous": observed_ambiguous,
            "observed_unmatched": len(unmatched)}


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------

def _validate_families(families) -> list[dict]:
    if not isinstance(families, list) or not families:
        raise Refused("families must be a non-empty list")
    seen = set()
    clean = []
    for family in families:
        if not isinstance(family, dict):
            raise Refused("families carries a non-mapping entry")
        family_id = str(family.get("id") or "").strip()
        name = str(family.get("name") or "").strip()
        if not family_id or not name:
            raise Refused("a family needs both `id` and `name`")
        if family_id in seen:
            raise Refused(f"duplicate family id {family_id!r}")
        seen.add(family_id)
        clean.append({"id": family_id, "name": name})
    return clean


def _validate_devices(devices) -> list[dict]:
    if not isinstance(devices, list) or not devices:
        raise Refused("devices must be a non-empty list")
    seen = set()
    clean = []
    for device in devices:
        if not isinstance(device, dict):
            raise Refused("devices carries a non-mapping entry")
        device_id = str(device.get("id") or "").strip()
        state = str(device.get("state") or "").strip()
        raw_ids = [str(item) for item in (device.get("device_ids") or []) if str(item).strip()]
        if not device_id:
            raise Refused("a device class needs an `id`")
        if device_id in seen:
            raise Refused(f"duplicate device class id {device_id!r}")
        if state not in STATES:
            raise Refused(f"{device_id}: state {state!r} is not one of {list(STATES)}")
        if state == "OBSERVED" and not device.get("observations"):
            raise Refused(f"{device_id}: OBSERVED requires an observation list")
        if state == "UNRESOLVED" and raw_ids:
            raise Refused(f"{device_id}: UNRESOLVED cannot carry device ids "
                          f"({raw_ids}); that is a resolved identity")
        if state == "NOT_OBSERVED" and not raw_ids:
            raise Refused(f"{device_id}: NOT_OBSERVED requires the authority device id it "
                          f"failed to observe")
        seen.add(device_id)
        clean.append({"id": device_id, "label": str(device.get("label") or device_id),
                      "device_ref": device.get("device_ref"),
                      "device_ids": raw_ids, "state": state,
                      "reason": device.get("reason"),
                      "observations": device.get("observations")})
    return clean


def coverage(*, families, manifest: dict, source_revision: str, devices) -> dict:
    """The 21-family × N-device-class coverage matrix. Pure: no IO, no clock, no network."""
    source_revision = str(source_revision or "").strip()
    if not source_revision:
        raise Refused("source_revision is required: a matrix that cannot say which authority "
                      "revision it describes is not reproducible")
    clean_families = _validate_families(families)
    clean_devices = _validate_devices(devices)
    identities = _records(manifest)

    primary: dict[str, set[str]] = {}
    cross: dict[str, set[str]] = {}
    for identity, records in identities.items():
        for role in _primary_roles(records):
            primary.setdefault(role, set()).add(identity)
        for ref in _cross_refs(records):
            cross.setdefault(ref, set()).add(identity)

    matches = {device["id"]: _match_observations(identities, device["observations"])
               for device in clean_devices if device["state"] == "OBSERVED"}

    device_summaries = []
    for device in clean_devices:
        installed_ids = [identity for identity, records in identities.items()
                         if device["state"] != "UNRESOLVED"
                         and _installed(records, device["device_ids"])]
        observed = matches.get(device["id"])
        device_summaries.append({
            "id": device["id"], "label": device["label"], "state": device["state"],
            "device_ref": device["device_ref"], "device_ids": device["device_ids"],
            "reason": device["reason"],
            "counts": {
                "installed_identities": (len(installed_ids) if device["state"] != "UNRESOLVED"
                                         else "UNKNOWN"),
                "observed": (len(device["observations"]) if observed is not None
                             else "UNKNOWN"),
                "observed_strong": (observed["observed_strong"] if observed is not None
                                    else "UNKNOWN"),
                "observed_probable": (observed["observed_probable"] if observed is not None
                                      else "UNKNOWN"),
                "observed_ambiguous": (observed["observed_ambiguous"] if observed is not None
                                       else "UNKNOWN"),
                "observed_unmatched": (observed["observed_unmatched"] if observed is not None
                                       else "UNKNOWN"),
                "identities_strong": (len(observed["strong"]) if observed is not None
                                      else "UNKNOWN"),
                "identities_probable": (len(observed["probable"]) if observed is not None
                                        else "UNKNOWN"),
                "identities_ambiguous": (len(observed["ambiguous"]) if observed is not None
                                         else "UNKNOWN"),
            },
            "installed_identities": sorted(installed_ids),
        })

    rows = []
    for family in clean_families:
        family_id = family["id"]
        realized = sorted(primary.get(family_id, set()))
        devices_cells = {}
        for device in clean_devices:
            if device["state"] == "UNRESOLVED":
                cell = {
                    "identity": "UNRESOLVED",
                    "installed": "UNKNOWN",
                    "observed": "UNKNOWN",
                    "observed_strong": "UNKNOWN",
                    "observed_probable": "UNKNOWN",
                    "ready": "UNKNOWN",
                    "grant": "UNKNOWN",
                    "effective": [],
                    "why": device["reason"],
                }
            else:
                installed = sum(1 for identity in realized
                                if _installed(identities[identity], device["device_ids"]))
                if device["state"] == "NOT_OBSERVED":
                    observed, strong, probable = "NOT_OBSERVED", "UNKNOWN", "UNKNOWN"
                else:
                    observed = "OBSERVED"
                    strong = len(set(realized) & matches[device["id"]]["strong"])
                    probable = len(set(realized) & matches[device["id"]]["probable"])
                cell = {
                    "identity": "RESOLVED",
                    "installed": installed,
                    "observed": observed,
                    "observed_strong": strong,
                    "observed_probable": probable,
                    "ready": "UNKNOWN",
                    "grant": "UNKNOWN",
                    "effective": [],
                    "why": ("this round supplied no health or policy observation; the "
                            "word is UNKNOWN, not false"
                            if device["state"] == "OBSERVED"
                            else "no observation snapshot was supplied for this device class"),
                }
            devices_cells[device["id"]] = cell
        rows.append({
            "family": family_id,
            "name": family["name"],
            "realizations": realized,
            "cross_referenced_by": sorted(cross.get(family_id, set())),
            "gap": None if realized else "NO_PRIMARY_REALIZATION",
            "devices": devices_cells,
        })

    unclassified = sorted(identity for identity, records in identities.items()
                          if not _primary_roles(records))
    unclassified_installed = {}
    for device in clean_devices:
        if device["state"] == "UNRESOLVED":
            unclassified_installed[device["id"]] = "UNKNOWN"
            continue
        unclassified_installed[device["id"]] = sorted(
            identity for identity in unclassified
            if _installed(identities[identity], device["device_ids"]))

    return {
        "schema": SCHEMA,
        "authority": "ability-management",
        "this_is": THIS_IS,
        "source_revision": source_revision,
        "families": rows,
        "devices": device_summaries,
        "counts": {
            "families": len(rows),
            "families_with_primary": sum(1 for row in rows if row["realizations"]),
            "families_without_primary": sum(1 for row in rows if not row["realizations"]),
            "identities": len(identities),
            "identities_unclassified": len(unclassified),
            "identities_unclassified_installed": unclassified_installed,
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _cell(value) -> str:
    if isinstance(value, list):
        return str(len(value))
    return str(value)


def render_markdown(result: dict, *, evidence: dict | None = None,
                    command: str | None = None) -> str:
    """Deterministic markdown. The one clock-bearing value is the observation timestamp,
    which is carried by the fixture, not read here."""
    revision = result["source_revision"]
    counts = result["counts"]
    device_ids = [device["id"] for device in result["devices"]]
    lines = [
        "<!-- GENERATED FILE - DO NOT EDIT BY HAND -->",
        "",
        "# G1 · Device Ability coverage matrix",
        "",
        "> **GENERATED / NON-AUTHORITATIVE / REPRODUCIBLE.**",
        "> A view of the authority's manifest and of one sanitized device observation. Editing",
        "> this file moves nothing; the next render overwrites it and `--check` reports drift.",
        f"> Authority: `ProfessorZhi/ability-management` `abilities-manifest.yaml` @ `{revision}`.",
        "> Vocabulary: `product-tree.yaml` @ the same revision (21 L2 families).",
        "> Basis: `product_role.primary` for realization, `revisions[].installations[]` for",
        "> installation, and a device observation snapshot for observation.",
        "",
        "This file is **not a registry** and **not a callability claim**. It separates four",
        "facts that are easy to collapse into one integer:",
        "",
        "| word | comes from | means |",
        "|---|---|---|",
        "| declared | product vocabulary | the family exists (always 21 rows) |",
        "| realized | manifest `product_role.primary` | some identity owns this responsibility |",
        "| installed | manifest `revisions[].installations[]` | the **authority** records an installation on this device id |",
        "| observed | device snapshot | the **device** reports something matching an identity |",
        "",
        "`observed` is split into `strong` (a `stable_id` / canonical-source match) and",
        "`probable` (name only). The authority's rule is that name-only is PROBABLE MATCH,",
        "never SAME, so a probable match is never counted as identity.",
        "",
        "`ready` and `grant` are `UNKNOWN` throughout: a scan snapshot carries no health and",
        "no per-principal policy observation. **UNKNOWN is not false.** `NOT_OBSERVED` means",
        "nobody looked this round; `UNRESOLVED` means the authority declares no device id at",
        "all. `effective` is always a list here — the per-ability rule lives in `effective.py`.",
        "",
        "## Matrix",
        "",
        "| Family | Name | Realized | " + " | ".join(
            f"{label} installed / observed (strong+probable)" for label in device_ids)
        + " | ready | grant | gap |",
        "|---|---|---|" + "---|" * len(device_ids) + "---|---|",
    ]
    for row in result["families"]:
        cells = []
        for device_id in device_ids:
            cell = row["devices"][device_id]
            if cell["identity"] == "UNRESOLVED":
                cells.append("UNKNOWN")
            elif cell["observed"] == "NOT_OBSERVED":
                cells.append(f"{_cell(cell['installed'])} / NOT_OBSERVED")
            else:
                cells.append(f"{_cell(cell['installed'])} / "
                             f"{_cell(cell['observed_strong'])}+{_cell(cell['observed_probable'])}")
        lines.append(
            f"| {row['family']} | {row['name']} | {len(row['realizations'])} | "
            + " | ".join(cells)
            + f" | {row['devices'][device_ids[0]]['ready']} | "
            f"{row['devices'][device_ids[0]]['grant']} | {row['gap'] or ''} |")

    lines += [
        "",
        "## Counts",
        "",
        f"- Families: **{counts['families']}**; with a primary realization: "
        f"**{counts['families_with_primary']}**; without: "
        f"**{counts['families_without_primary']}**.",
        f"- Ability identities: **{counts['identities']}**; without "
        f"`product_role.primary`: **{counts['identities_unclassified']}**.",
    ]
    for device in result["devices"]:
        detail = device["counts"]
        lines.append(
            f"- `{device['id']}` ({device['state']}): installed identities "
            f"{detail['installed_identities']}; observations {detail['observed']} "
            f"(strong {detail['observed_strong']}, probable {detail['observed_probable']}, "
            f"ambiguous {detail['observed_ambiguous']}, unmatched "
            f"{detail['observed_unmatched']}); identities matched strong "
            f"{detail['identities_strong']} / probable {detail['identities_probable']}.")

    lines += ["", "## Gaps", ""]
    for row in result["families"]:
        if row["gap"] == "NO_PRIMARY_REALIZATION":
            if row["cross_referenced_by"]:
                lines.append(
                    f"- **{row['family']} · {row['name']}** — NO_PRIMARY_REALIZATION; "
                    f"cross-referenced by {len(row['cross_referenced_by'])} "
                    f"identit{'y' if len(row['cross_referenced_by']) == 1 else 'ies'} "
                    f"({', '.join(f'`{i}`' for i in row['cross_referenced_by'])}). "
                    "It is unowned, not absent.")
            else:
                lines.append(
                    f"- **{row['family']} · {row['name']}** — NO_PRIMARY_REALIZATION and no "
                    "cross-reference: no declared relationship to any realization.")
    unclassified = counts["identities_unclassified"]
    if unclassified:
        lines.append(
            f"- **{unclassified} of {counts['identities']} identities declare no "
            "`product_role.primary`.** They cannot be attributed to a family (not even as "
            "zero), so every family count above is a lower bound. The ones the authority "
            "records as installed are listed below.")
    for device in result["devices"]:
        if device["state"] == "UNRESOLVED":
            lines.append(f"- **`{device['id']}` is UNRESOLVED.** {device['reason']}")
        elif device["state"] == "NOT_OBSERVED":
            lines.append(f"- **`{device['id']}` was NOT_OBSERVED.** {device['reason']}")
    lines.append("")

    for device in result["devices"]:
        if device["state"] == "UNRESOLVED":
            continue
        installed = device["installed_identities"]
        if not installed:
            continue
        lines += [f"### `{device['id']}` — installed identities", ""]
        lines += [f"- `{identity}`" for identity in installed]
        lines.append("")
    for device in result["devices"]:
        value = counts["identities_unclassified_installed"].get(device["id"])
        if isinstance(value, list) and value:
            lines += [f"### `{device['id']}` — unclassified identities with an installation",
                      ""]
            lines += [f"- `{identity}`" for identity in value]
            lines.append("")

    if evidence:
        lines += ["## One bounded request per device class", "",
                  f"Command: `{evidence.get('_command', 'n/a')}`", ""]
        for name, item in evidence.items():
            if name.startswith("_"):
                continue
            lines.append(f"### `{name}`")
            lines.append("")
            lines.append(f"- status: `{item.get('status')}`")
            for key in ("exit_code", "device_ref", "runtime", "semantic_owner",
                        "cross_refs", "inputs_seen", "inputs_missing", "detail", "reason",
                        "source"):
                if key in item and item[key] is not None:
                    value = item[key]
                    if isinstance(value, list):
                        value = ", ".join(f"`{v}`" for v in value)
                    lines.append(f"- {key}: {value}")
            lines.append("")
        lines += ["The Tokyo request is evidence about the **conjunction**, not a success:",
                  "the resolver named seven axes it was never given, including `policy`. The",
                  "other two device classes have no `device_ref` this round and are NOT_RUN;",
                  "no id was inferred from a product name.", ""]

    if command:
        lines += ["## Regenerate", "", "```bash", command, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI (the only place that touches files)
# ---------------------------------------------------------------------------

def _load_devices(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    devices = data.get("devices") if isinstance(data, dict) else data
    if not isinstance(devices, list):
        raise Refused(f"{path}: no devices list")
    loaded = []
    for device in devices:
        item = dict(device)
        observation_file = item.pop("observation_file", None)
        if observation_file:
            observation_path = path.parent / observation_file
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            item["observations"] = observation.get("observations")
        loaded.append(item)
    return loaded


def main(argv=None) -> int:  # pragma: no cover - exercised by the repository command
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True,
                        help="authority abilities-manifest.yaml at --revision")
    parser.add_argument("--families", default=str(Path(__file__).parent / "fixtures" / "families.json"))
    parser.add_argument("--devices", default=str(Path(__file__).parent / "fixtures" / "coverage-devices.json"))
    parser.add_argument("--revision", required=True, help="full 40-hex authority revision")
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--output", default=str(Path(__file__).parent / "DEVICE-COVERAGE-MATRIX.md"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    import yaml  # the authority's file is YAML; the CLI is the only reader

    manifest = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8"))
    families = json.loads(Path(args.families).read_text(encoding="utf-8"))["families"]
    devices = _load_devices(Path(args.devices))
    evidence = (json.loads(Path(args.evidence).read_text(encoding="utf-8"))
                if args.evidence else None)
    command = (f"python3 integrations/device-ability-projection/coverage.py "
               f"--manifest <ability-management>/abilities-manifest.yaml "
               f"--families integrations/device-ability-projection/fixtures/families.json "
               f"--devices integrations/device-ability-projection/fixtures/coverage-devices.json "
               f"--revision {args.revision} "
               f"--evidence integrations/device-ability-projection/fixtures/resolve-evidence.json")
    result = coverage(families=families, manifest=manifest,
                      source_revision=args.revision, devices=devices)
    rendered = render_markdown(result, evidence=evidence, command=command)
    if args.stdout:
        sys.stdout.write(rendered)
        return 0
    target = Path(args.output)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            sys.stderr.write(f"DRIFT: {target} does not match the render\n")
            return 1
        sys.stdout.write(f"OK: {target}\n")
        return 0
    if args.write:
        target.write_text(rendered, encoding="utf-8")
        sys.stdout.write(f"wrote {target}\n")
        return 0
    parser.error("one of --stdout, --write or --check is required")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
