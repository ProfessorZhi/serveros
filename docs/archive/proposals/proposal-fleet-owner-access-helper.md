# Fleet owner-access configuration and project paths

## Problem Statement

`fleet_mcp/config.py` currently binds devices, native families and project-level
creation policy. The V1 owner route additionally needs one explicit, locally
authored maximum-permission profile for a bound runtime, plus safe paths for
creating projects below named owner-selected roots. Caller-provided authority
or arbitrary paths must not become configuration.

## Goals

- Introduce `fleet_mcp/owner_access.py` with `OwnerAccessProfile`,
  `OwnerAccessError`, `parse_owner_access(raw)` and
  `build_project_path(profile, root_ref, name, *, local_check)`.
- Require `owner_approved` to be exactly `True`, a nonempty project-root mapping,
  and `permission_mode` equal to `bypassPermissions` (also its omitted default).
- Plan one safe project directory under a named configured root on POSIX or
  Windows; validate existing local ancestors without following symlinks.
- Make configuration immutable even when the caller later mutates its input.

## Non-Goals

No configuration loading, credentials, grants, provider calls, mkdir, deployment,
or persistence. Other work adds runtime/principal policy and actual project
creation. A path check does not close the check-to-create race; the creation
owner must atomically enforce the checked boundary.

## Design

The existing `fleet_mcp/runtime_create_policy.py` uses a `ValueError` subtype
for strict config compilation. The new `OwnerAccessError(ValueError)` follows
that convention without coupling the helper to HTTP or Fleet refusals.

`OwnerAccessProfile` is a frozen dataclass with `owner_approved`,
`project_roots: Mapping[str, str]`, and `permission_mode` fields. Both direct
construction and parsing validate the same invariants, copy roots into a
`MappingProxyType`, and reject unknown parse fields. `None` parses as `None`.

Root references are ASCII slugs of 1–64 characters beginning with an
alphanumeric, then alphanumerics, underscores or hyphens. Paths must be exact
absolute POSIX or drive-qualified Windows paths, without interpolation,
relative segments, control characters or Windows device namespaces. Windows
paths use `PureWindowsPath` even when parsing on Linux. Duplicate or overlapping
roots are rejected lexically, case-insensitively for Windows.
POSIX paths use one leading slash; Windows paths require an explicit drive
and reject UNC, device, alternate-stream and reserved-name components. Relative
components are checked before pure-path normalization can erase them. Accepted
Windows separators are normalized to backslashes for comparison and output.

Project names are single portable ASCII slug components of 1–128 characters,
beginning with an alphanumeric, then alphanumerics, dot, underscore or hyphen.
Windows reserved names, ADS, separators, trailing dot/space and traversal are
rejected on every platform. Only configured `root_ref` values may be selected.

`local_check=False` performs no filesystem IO. `local_check=True` requires a
matching local path flavor, walks each root ancestor with `lstat`, rejects
symlinks and reparse points, requires a real existing directory at the root,
and rejects any existing target, including a dangling symlink. It returns the
same pure target string and makes no filesystem changes.

## Alternatives

- A mutable dict in a frozen dataclass would permit unreviewed policy changes.
- `Path.resolve()` would follow symlinks before the boundary can reject them.
- Host-native `Path` alone would misparse Windows configuration on Linux.
- Accepting arbitrary caller paths would bypass the named-root contract.

## Open Questions

None for this pure helper. Runtime policy assembly and atomic creation remain
explicit integration responsibilities; the helper itself grants no authority.

## Proposal Reviews

Round 1: warning — pure-path construction erases `.` segments and aliases;
the raw component check must precede normalization. Confirmed and clarified.
No critical issues. Round 2: warning — a Windows UNC root is not a local
drive-qualified root and reserved root components admit native aliases. Require
drive-qualified roots and validate native components. Confirmed and clarified.
After these fixes no critical or warning issues remain. The parent confirmed
the ASCII root-reference contract and the default permission mode.
