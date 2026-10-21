"""Prepare one owner-max setup and exact rollback bytes; never copy credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .config import load_config, provider_key, load_owner_max
from .node.connector import load_node_config
from .owner_access import parse_owner_access
from .scopes import ALLOWED_SCOPES
from .tools import SERVED_TOOLS
from .version import TOOL_SURFACE_REVISION
from .privacy import redact_text


class _UniqueYaml(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("configuration keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueYaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
_SECRET_KEYS = {"token", "secret", "password", "passwd", "passphrase", "credential",
                "apikey", "accesskey", "secretkey", "accesstoken", "refreshtoken",
                "clientsecret", "privatekey", "authorization", "cookie"}


def _plain(value, depth=0, labels=False):
    # Also breaks YAML alias sharing: editing one provider must not edit another.
    if depth > 32:
        raise ValueError("configuration nesting or alias cycle exceeds its bound")
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("configuration keys must be strings")
            if not labels and re.sub(r"[^a-z0-9]", "", key.lower()) in _SECRET_KEYS:
                raise ValueError("configuration must name credential anchors, never credential values")
            if key == "endpoint" and isinstance(child, str):
                parsed = urlsplit(child)
                if (parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment):
                    raise ValueError("endpoint credentials must remain in their existing anchors")
            result[key] = _plain(child, depth + 1, labels=key in {
                "project_roots", "owner_max", "operator_grants", "profiles", "providers", "create_allow"})
        return result
    if isinstance(value, list):
        return [_plain(child, depth + 1) for child in value]
    return value


def _read(path):
    content = Path(path).read_bytes()
    try:
        text = content.decode("utf-8")
        if redact_text(text)[1]:
            raise ValueError("configuration contains a credential-shaped value; keep it in its anchor")
        data = yaml.load(text, Loader=_UniqueYaml)
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValueError("configuration is not valid UTF-8 YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    return content, _plain(data)


def _profile_dict(profile):
    return {"owner_approved": True, "project_roots": dict(profile.project_roots),
            "permission_mode": profile.permission_mode}


def _output(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("output must be an absolute path without traversal")
    for parent in reversed(path.parents):
        try:
            observed = parent.lstat()
        except OSError as exc:
            raise ValueError("output parent directories must already exist") from exc
        if (not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode)
                or getattr(observed, "st_file_attributes", 0) & 0x400):
            raise ValueError("output ancestors must be real directories")
    if path.exists() or path.is_symlink():
        raise ValueError("output must be a new exclusive directory")
    return path


def _write(path, content):
    handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(fleet_source, request, output, *, node_sources=None):
    """Validate before writing; return a non-secret activation/readback plan."""
    if (not isinstance(request, dict) or set(request) -
            {"owner_max", "profiles", "multiplex_native_families"}):
        raise ValueError("owner setup request has unknown fields")
    before, raw = _read(fleet_source)
    current = load_config(fleet_source)
    owners = load_owner_max(request.get("owner_max", {}))
    if not owners:
        raise ValueError("name at least one owner and exact runtime namespace")
    requested_profiles = request.get("profiles")
    if not isinstance(requested_profiles, dict) or not requested_profiles:
        raise ValueError("owner setup needs explicit profiles and named roots")
    multiplex = request.get("multiplex_native_families", [])
    if (not isinstance(multiplex, list)
            or any(not isinstance(key, str) or key not in current.providers for key in multiplex)
            or len(set(multiplex)) != len(multiplex)):
        raise ValueError("multiplex selection must name existing binding keys")
    providers = dict(current.providers)
    raw_keys = {}
    for key, entry in raw["providers"].items():
        device, _, keyed_runtime = key.partition("|")
        canonical = provider_key(device, str(entry.get("runtime") or keyed_runtime or "claude"))
        if canonical in raw_keys:
            raise ValueError("duplicate canonical provider binding")
        raw_keys[canonical] = key
    profiles = {}
    for key, proposed in requested_profiles.items():
        if key not in providers:
            raise ValueError("owner profiles require existing exact bindings")
        profile = parse_owner_access(proposed)
        if profile is None:
            raise ValueError("an owner profile cannot be omitted")
        prior = providers[key].owner_access
        if prior is not None and prior != profile:
            raise ValueError("existing owner roots require an explicit separate migration")
        profiles[key] = profile
        providers[key] = replace(providers[key], owner_access=profile,
                                 multiplex_native_families=(True if key in multiplex
                                                            else providers[key].multiplex_native_families))
        raw_key = raw_keys[key]
        raw["providers"][raw_key] = dict(raw["providers"][raw_key])
        raw["providers"][raw_key]["owner_access"] = _profile_dict(profile)
        if key in multiplex:
            raw["providers"][raw_key]["multiplex_native_families"] = True
    if set(multiplex) - set(profiles):
        raise ValueError("multiplex updates must belong to selected owner profiles")
    merged_owners = dict(current.policy.owner_max)
    for principal, namespaces in owners.items():
        merged_owners[principal] = tuple(dict.fromkeys((*merged_owners.get(principal, ()), *namespaces)))
    candidate = replace(current, providers=providers,
                        policy=replace(current.policy, owner_max=merged_owners))
    used = set()
    for namespaces in owners.values():
        for namespace in namespaces:
            device, _, runtime = namespace.partition("|")
            binding = candidate.namespace_binding(device, runtime or "claude")
            used.add(provider_key(binding.device_ref, binding.runtime))
    if set(profiles) - used:
        raise ValueError("each owner profile must be selected by a named owner")
    raw.setdefault("policy", {})["owner_max"] = {p: list(v) for p, v in merged_owners.items()}
    node_documents = {}
    for key, source in (node_sources or {}).items():
        if key not in profiles:
            raise ValueError("Node setup must belong to a selected actual binding")
        original, document = _read(source)
        node = load_node_config(source)
        binding = providers[key]
        if node.device_ref != binding.device_ref or node.runtime != binding.runtime:
            raise ValueError("Node source identity differs from the selected binding")
        replace(node, owner_access=profiles[key])
        document["owner_access"] = _profile_dict(profiles[key])
        node_documents[key] = (original, yaml.safe_dump(document, sort_keys=False).encode())
    out = _output(output)
    fleet_bytes = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode()
    plan = {"schema": "serveros/fleet-owner-setup.v1",
            "fleet_config": str(out / "fleet-mcp.owner.yaml"),
            "rollback_fleet_config": str(out / "fleet-mcp.before.yaml"),
            "node_configs": {}, "source_sha256": hashlib.sha256(before).hexdigest(),
            "candidate_sha256": hashlib.sha256(fleet_bytes).hexdigest(),
            "expected": {"tools": len(SERVED_TOOLS), "surface_revision": TOOL_SURFACE_REVISION,
                         "durability": "AGENT_CONTROL_STORE",
                         "required_owner_scopes": list(ALLOWED_SCOPES),
                         "issuer_grantable_scopes": list(current.auth.grantable_scopes)},
            "entry": "bin/serveros-fleet-owned",
            "missing_node_configs": sorted(key for key in profiles if providers[key].kind == "fleet-node"
                                            and key not in node_documents),
            "state": "PREPARED_NOT_APPLIED"}
    out.mkdir(mode=0o700)
    _write(out / "fleet-mcp.owner.yaml", fleet_bytes)
    _write(out / "fleet-mcp.before.yaml", before)
    for index, (key, (original, document)) in enumerate(sorted(node_documents.items())):
        new = out / f"node-{index}.owner.yaml"
        _write(new, document)
        _write(out / f"node-{index}.before.yaml", original)
        plan["node_configs"][key] = str(new)
    _write(out / "plan.json", json.dumps(plan, ensure_ascii=False, indent=2).encode())
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-config", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--node-config", action="append", default=[], metavar="BINDING=PATH")
    args = parser.parse_args(argv)
    try:
        nodes = {}
        for entry in args.node_config:
            key, separator, source = entry.partition("=")
            if not separator or key in nodes:
                raise ValueError("Node sources must be unique BINDING=PATH entries")
            nodes[key] = Path(source)
        result = prepare(args.fleet_config, json.loads(args.request.read_text()), args.out,
                         node_sources=nodes)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
