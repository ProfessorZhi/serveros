# Windows — a DIFFERENT adapter, not a port

**Nothing here is installed, running or verified on Windows.** `systemd_user.cpp`
is Linux-only, and no Windows equivalent exists in this tree. This document is a
**contract**: it states what a Windows adapter must satisfy, and what is
UNKNOWN about it. It is not evidence that anything works there.

## 1. Why this cannot be a port

The supervisor's process supervision is delegated to `systemd --user` on
purpose (`contract/desired-state.v1.md`, §20: *systemd IS the supervisor*). On
Windows the equivalent facility is the **Service Control Manager**, and it is not
the same facility wearing a different name:

| | `systemd --user` | Windows SCM |
|---|---|---|
| scope | one user, session/lingering | machine-wide, a service has its own account (LocalSystem / NetworkService / a named account) |
| identity | the invoking UID; `SO_PEERCRED` on the status socket | a per-service security descriptor; there is no «the user's own service namespace» |
| start | login, or `loginctl enable-linger` | machine boot, or an explicit start; a *user's* service at *their* login is Task Scheduler, a different API with a different security model |
| failure | `Restart=on-failure`, `StartLimitBurst` | per-service failure actions, configured in the SCM, not in the service |
| enumeration | `systemctl --user list-units` | SCM handles scoped by an access check |

So «Core-Lite on Windows» has to answer a question the Linux side never asks:
**whose service is it**, when the OS has no per-user service namespace. That is a
design decision with a security consequence, not a translation.

## 2. What a Windows adapter MUST satisfy (and cannot fake)

1. **A read-only observation must stay read-only.** Whatever the SCM API
   exposes, the adapter produces `Observed` values and never a start, stop or
   restart in V1.
2. **The allowlist travels.** The unit-identity equivalent must be a closed
   allowlist of shapes the adapter can represent, not a denylist — the same rule
   `valid_unit_id` enforces, re-derived for whatever names the SCM uses.
3. **Identity is not inferred.** A `systemd --user` deployment is one user; a
   Windows deployment must state **which principal** the observations are about,
   and refuse rather than assume when it cannot be established.
4. **UNKNOWN is a word, not a zero.** A counter the adapter cannot read is
   UNAVAILABLE with a reason, exactly as the Linux collector's invariant says.
5. **PSI does not exist.** `psi.*` observations are UNAVAILABLE on Windows, by
   construction. A profile declaring them on Windows parses (the declaration is
   legal) and the runtime reports UNAVAILABLE — never zero, never silently
   dropped.

## 3. What is shared, and what is not

| piece | shareable |
|---|---|
| the **pure planner** (`plan.hpp`) — cycle detection, owner rules, allowlist, `observe_only` | **yes**, it is clock-free and provider-free |
| the **profile reader** (`corelite/profile.cpp`) | **yes**, it is pure and platform-tagged |
| the **collector's contract** (`node-resource-snapshot.v1`) | **yes** as a contract; the *Linux* readers obviously are not |
| `systemd_user.cpp` | **no** |
| the status socket (`status_socket.hpp`, `flock`/`SO_PEERCRED`) | **no** — Windows needs `LockFileEx` and a different peer-identity mechanism |

## 4. Status of this document

| claim | status |
|---|---|
| the profile schema is platform-tagged and parses for `"platform": "windows"` | **VERIFIED** — a unit check in `tests/test_profile.cpp` |
| a Windows adapter exists | **NO — does not exist** |
| SCM / Task Scheduler identity behaviour on a real Windows host | **UNKNOWN — not measured; no Windows host was touched** |
| Core health reporting on Windows | **NOT IMPLEMENTED** |

**A Linux test proves nothing about Windows.** The next honest step is to run a
read-only probe on the Windows host itself (the fleet's `device:work-fj`), which
this round did not do and is not authorised to do.
