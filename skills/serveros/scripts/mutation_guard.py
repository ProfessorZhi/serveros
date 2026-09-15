#!/usr/bin/env python3
"""§18/§26 · What this codebase is allowed to do to the world.

Two questions, and they are not the same one.

    §18 architecture  may this layer reach the host at all?
    §26 safety        may this code path destroy something?

The temptation is to answer both with one `grep` for a list of words, and that
approach fails in both directions at once. `grep -rn "docker rm" .` finds
``mcp/agent_mcp.py`` line 19, which is a docstring saying there is no such
thing; and it does not find ``runtime/daemon.py``'s actuator, whose command is
assembled in a variable and handed to a runner. The first is a false positive
that trains people to ignore the gate, and the second is the true positive the
gate exists for.

So this reads **parsed source**, not text:

  * comments and docstrings are not code and cannot be mistaken for it, because
    they are not call sites;
  * an argv that is a literal is read as the command it is;
  * an argv that is **not** a literal is reported as `unresolved_argv`, which is
    a gap in this checker's knowledge and never a pass.

That last point is the whole design. A scanner that only understands literals
reports a clean tree for the same reason a smoke detector reports a clean house
when it is unplugged. `runtime/daemon.py` builds its restart command from the
registry and parks it in a local before calling `self.runner.run(command, 60.0)`
— invisible to a literal-only scan, and the single most consequential command
in the product. Unresolved argv is therefore a first-class result and the
`actuators` contract has to account for every one of them.

The rule, in one sentence
-------------------------
**Every place this code can change the world is declared once, with a reason,
and the declaration is checked against the code in both directions.**

  * a hit with no declaration is `UNDECLARED_ACTUATOR` — new reachability
    nobody reviewed;
  * a declaration with no hit is `STALE_ACTUATOR` — a permission that outlived
    the code it was granted for, which is the "declared but nothing emits it"
    pathology this project keeps finding, applied to the security surface
    rather than the data surface.

Neither direction is optional. One-way checking means an exemption can be
written once and silently keep covering a file that has since grown a second
call site.

§18's layer rule
----------------
The MCP layer does not run host commands. Its job is to transcribe what the
daemon observed; the daemon is the producer of every host fact, and a second
producer in the MCP process would be a second answer waiting to disagree with
the first. This is enforced as an absolute — no exemption table entry may cover
an MCP-layer host read — because the whole point of the rule is that the MCP
process holds no host knowledge of its own. `mcp/node_mcp.py` reaching the
daemon over a Unix socket is the shape that is correct.

Read-only, and it stays that way
--------------------------------
Nothing here writes, moves, or repairs. This module's own source is scanned by
the same scanner (see `self_scan`), so the rule it enforces on everything else
is enforced on it.
"""

from __future__ import annotations

import ast
import builtins
import json
import sys
from pathlib import Path

SCHEMA = "serveros/mutation-guard.v1"

# §18, verbatim: the commands the MCP layer may not run directly. These are the
# host *readers* — the ones that would turn an MCP process into a second
# observer of the machine.
HOST_READERS = ("docker", "df", "free", "ss", "du", "find")

# §26, verbatim: the mutation vocabulary. `restart` is deliberately absent —
# see `_docker_verb` for why the distinction is kept rather than collapsed.
MUTATING_DOCKER_VERBS = ("stop", "kill", "rm", "rmi", "prune", "down")
MUTATING_DOCKER_SUBJECTS = {
    "volume": ("rm", "prune"),
    "network": ("rm", "prune"),
    "system": ("prune",),
    "builder": ("prune",),
    "image": ("rm", "prune"),
    "container": ("rm", "prune", "kill", "stop"),
}
MUTATING_SYSTEMCTL_VERBS = ("stop", "kill", "restart", "disable", "mask")

# `ip` is the third binary with the `docker` shape — mostly a reader, with
# mutating forms — and it is handled before the generic reader label for the
# same reason `docker` is: reading it as a read because most uses are
# `ip addr show` is how `ip route add` becomes a mutation nothing can find.
#
# Enumerated per object rather than as one word list, because `ip` is
# `ip [flags] OBJECT COMMAND` and `show` mutates nothing while `add` on the
# same object does. An object not named here is reported as a read of that
# object; the alternative — treating an unknown object as possibly-mutating —
# would report `ip -j addr show` as unresolved and a report full of those is
# one nobody reads.
MUTATING_IP_COMMANDS = {
    "link": ("set", "add", "del", "delete", "change", "replace"),
    "addr": ("add", "del", "delete", "change", "replace", "flush"),
    "address": ("add", "del", "delete", "change", "replace", "flush"),
    "route": ("add", "del", "delete", "change", "replace", "append",
              "prepend", "flush"),
    "rule": ("add", "del", "delete"),
    "neigh": ("add", "del", "delete", "change", "replace", "flush"),
    "neighbour": ("add", "del", "delete", "change", "replace", "flush"),
    "netns": ("add", "del", "delete", "attach", "set"),
    "tunnel": ("add", "del", "delete", "change"),
    "vlan": ("add", "del", "delete"),
    "maddress": ("add", "del", "delete"),
    "mroute": ("add", "del", "delete"),
    "token": ("set", "del", "delete"),
}

# Calls that change the world wherever they appear, whatever they are given.
MUTATING_OS_CALLS = {
    "os.kill": "process_termination",
    "os.killpg": "process_termination",
    "os.system": "exec",
    "os.popen": "exec",
    "os.execl": "exec",
    "os.execle": "exec",
    "os.execlp": "exec",
    "os.execv": "exec",
    "os.execve": "exec",
    "os.execvp": "exec",
    "os.execvpe": "exec",
    "os.remove": "fs_mutation",
    "os.unlink": "fs_mutation",
    "os.rmdir": "fs_mutation",
    "os.rename": "fs_mutation",
    "os.replace": "fs_mutation",
    "os.chmod": "fs_mutation",
    "os.chown": "fs_mutation",
    "os.truncate": "fs_mutation",
    "shutil.rmtree": "fs_mutation",
    "shutil.move": "fs_mutation",
    "pathlib.Path.unlink": "fs_mutation",
    "pathlib.Path.rmdir": "fs_mutation",
    "pathlib.Path.rename": "fs_mutation",
    "pathlib.Path.replace": "fs_mutation",
    "pathlib.Path.write_text": "fs_write",
    "pathlib.Path.write_bytes": "fs_write",
}

# The exec family. Named apart from MUTATING_OS_CALLS because whether one of
# these mutates anything is a property of its argument, and the argument is
# read separately.
EXEC_CALLS = {
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen", "subprocess.getoutput",
    "subprocess.getstatusoutput",
}

# Method calls that are a mutation by name alone. `unlink`, `rmdir` and `rename`
# are not method names `str`, `int` or `dict` have, so there is nothing else
# this could be. `send_signal`/`terminate`/`killpg` are likewise process-only.
UNAMBIGUOUS_METHODS = {
    "killpg": "process_termination",
    "terminate": "process_termination",
    "send_signal": "process_termination",
    "unlink": "fs_mutation",
    "rmdir": "fs_mutation",
    "rename": "fs_mutation",
}

# `kill` is on both `Popen` and — nothing else in the standard library this
# codebase uses, but a reviewer should not have to check that. It is listed
# with `replace` below as arity-resolved rather than name-resolved.
#
# `.replace()` is the case that made this scanner get its own arity rule.
# Thirteen of the fourteen `replace` call sites in this repository are
# `str.replace` — the `+00:00` to `Z` idiom in a dozen timestamp formatters —
# and two are `Path.replace`, the rename half of an atomic write. The name is
# identical and the receiver cannot be typed statically.
#
# The arity can: `Path.replace(target)` takes exactly one positional argument
# and no keywords, while `str.replace(old, new[, count])` takes two or three
# and `datetime.replace(...)` is keyword-only. Measured on the whole tree, that
# splits 40/4/2 with no ambiguity — so the rule is the documented signatures of
# the standard library, not a guess about the receiver's name. Guessing from
# the name (`tmp`) is what this file is written to avoid.
ARITY_RESOLVED_METHODS = {
    "replace": "fs_mutation",   # Path.replace(target): 1 positional, no keywords
    "kill": "process_termination",  # Popen.kill()
}

# The binaries whose presence as the first word of *any* call's first argument
# makes that call a command handoff. `runtime/daemon.py` builds
# `["docker", "restart", container]` and passes it to `self.runner.run`, and a
# scanner that only looked at `subprocess.*` would report that the product
# contains no actuator at all — the smoke detector reporting a clean house
# because it is unplugged.
#
# The list is deliberately only these names. Any call whose first argument is a
# string list would otherwise be a hit, and a gate nobody can read is a gate
# nobody reads.
HANDOFF_BINARIES = (HOST_READERS + ("docker", "systemctl", "ip", "pkill",
                                    "killall", "kill", "rm", "rmdir", "shred",
                                    "shutdown", "reboot", "poweroff", "halt"))

# Names that are a Python interpreter rather than a host command. Running a
# script through `sys.executable` is how the tests invoke the CLI; it is not a
# host read and it is not a mutation, and calling it either would be a finding
# that is wrong about the thing it names.
INTERPRETERS = ("python", "python3", "sys.executable")

# `subprocess.run(..., shell=True)` with a string is a shell in this process.
# Not banned — but it is never a host *read* the MCP layer may perform, and it
# makes argv analysis impossible, so it is reported as unresolved.
SHELL_KWARG = "shell"

# Read from the interpreter rather than written out, so the list cannot be a
# stale copy of a language's builtins. Membership is asked of `dir(builtins)`
# once at import; the rule it serves is one line — a builtin does not exec.
_BUILTIN_NAMES = frozenset(dir(builtins))


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for an attribute chain of plain names, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _static_argv(node: ast.AST | None) -> list[str | None] | None:
    """A call's first argument as a command, position by position.

    Each element is the string constant it is, or `None` where the element's
    value is not statically known. **Positions are preserved.** Collapsing an
    unknown element instead of holding its slot would shift every later word
    left, and `["docker", <unknown>, name]` would then read as `docker name` —
    a command that does not exist, reported with confidence.

    `None` for the whole thing means the outer shape is not a command at all
    (a bare Name with no binding, an f-string, a `*`-unpacked list). Callers
    treat that differently from a hole at position 1, and the difference is the
    point: `runtime/daemon.py` hands `command` to a runner after binding it to
    a literal in the same function, so the binding is followed; a parameter
    that arrives from elsewhere leaves a hole and is reported as one.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split()
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str | None] = []
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                out.append(element.value)
            elif isinstance(element, ast.Starred):
                # `*args` is an unknown number of positions rather than one
                # unknown position, so the list ends here. What came before it
                # is still faithful — `["git", "-C", repo, *args]` really does
                # run git — and the caller is told the rest is unread.
                return out
            else:
                out.append(None)
        return out
    return None


def _bare_words(prefix: list[str]) -> list[str]:
    """The words after a command name, with flags dropped.

    `docker --context foo rm` and `systemctl --user restart x` both put their
    verb behind flags, so the verb is the first bare word rather than the
    second argument.
    """
    return [a for a in prefix[1:] if not a.startswith("-")]


def _docker_verb(prefix: list[str]) -> tuple[str | None, bool]:
    """(mutating verb, whether a verb was found at all).

    The two are told apart because they lead to opposite conclusions. "There is
    a verb and it is not mutating" is `docker inspect`, a read. "There is no
    verb this scanner can see" is `["docker", <parameter>]`, and reporting it as
    a read would be the silent-clean failure.

    `docker restart` is deliberately NOT in the mutating set, and the reason is
    not convenience. It returns a container to the state its registration
    declares; it destroys nothing, the container is the same container, and it
    keeps its writable layer. §26 names stop/kill/rm/rmi/prune/volume rm/network
    rm — every one of which either ends a process this code does not own or
    removes bytes. Collapsing `restart` into that list is how a list stops
    being read.
    """
    words = _bare_words(prefix)
    if not words:
        return None, False
    if words[0] in MUTATING_DOCKER_VERBS:
        return words[0], True
    subject = MUTATING_DOCKER_SUBJECTS.get(words[0])
    if subject:
        # `docker volume` with the sub-subcommand unread is not `docker volume`
        # the read; it is a coin flip between `ls` and `rm`.
        if len(words) < 2:
            return None, False
        return (f"{words[0]} {words[1]}", True) if words[1] in subject else (None, True)
    return None, True


def _systemctl_verb(prefix: list[str]) -> tuple[str | None, bool]:
    words = _bare_words(prefix)
    if not words:
        return None, False
    return (words[0] if words[0] in MUTATING_SYSTEMCTL_VERBS else None), True


def _ip_verb(prefix: list[str]) -> tuple[str | None, bool]:
    """(mutating command, whether the OBJECT was found at all).

    The two are told apart for the same reason `_docker_verb` tells them apart,
    with the object standing where docker's verb stands. `["ip", <flag>]` with
    the object unread is a coin flip between `show` and `route add`, and
    calling it a read is the silent-clean failure.

    Flags are dropped by `_bare_words` before either word is read, which is
    what makes `ip -j route show table all` and `ip -4 addr show` work: the
    object is the first bare word and not the second argument.
    """
    words = _bare_words(prefix)
    if not words:
        return None, False
    commands = MUTATING_IP_COMMANDS.get(words[0])
    if commands is None:
        return None, True                       # a read of that object
    if len(words) < 2:
        return None, False                      # object read, command unread
    return (f"{words[0]} {words[1]}" if words[1] in commands else None), True


def classify_argv(argv: list[str | None]) -> tuple[str, str, bool]:
    """(kind, verb, complete) for a first argument read as a command.

    `complete` is False when a word the classification depended on was not
    statically known. It is returned rather than folded in because the two
    readings are different statements — "this runs `docker`, subcommand
    unread" and "this runs `docker restart`" — and only one of them is a fact
    about what the code does.
    """
    if not argv or argv[0] is None:
        return "unresolved_argv", "<argv0>", False
    binary = Path(argv[0]).name
    complete = all(w is not None for w in argv)
    # Everything up to the first unknown word. A verb is only read from this
    # prefix: reading past a hole would take the word after it for the word
    # that was missing, which is how a scan reports `docker name` with a
    # straight face.
    prefix = list(argv[:next((i for i, w in enumerate(argv) if w is None),
                             len(argv))])

    if argv[0] in INTERPRETERS or binary.startswith("python"):
        # The script it runs is not read from here, and this says so rather
        # than calling the interpreter a host read.
        return "exec", "python", False
    if binary in ("sh", "bash", "zsh", "dash"):
        return "exec", binary, False
    # `docker` and `find` are host READERS *and* have mutating forms, so the
    # verb is read before the reader label is applied. The order is the whole
    # correctness of this function: `HOST_READERS` is a scope rule (§18 — the
    # MCP layer may hold no host knowledge of its own), and reading it as a
    # KIND rule makes `docker rm` a host read. The mutating branch below then
    # never runs, `mutation` becomes a kind nothing can produce, and §26's
    # "no reachable destroyer" is confirmed by a line of code that cannot
    # execute. That is this round's own pathology, so it is written down here
    # rather than left to be rediscovered.
    if binary == "docker":
        verb, found = _docker_verb(prefix)
        if verb:
            return "mutation", f"docker {verb}", complete
        if not found:
            return "unresolved_argv", "docker <verb>", False
        # A docker READ. Which label it gets is a scope question, not a
        # command question: `host_reader` is the stricter of the two and is
        # what the MCP-layer rule matches on, so it wins wherever the contract
        # forbids the binary outright.
        return ("host_reader" if "docker" in HOST_READERS else "exec"), "docker", complete
    if binary in HOST_READERS:
        if binary == "find":  # same shape, same reason as `docker` above
            if "-delete" in prefix:
                return "mutation", "find -delete", complete
            return "host_reader", "find", complete
        return "host_reader", binary, complete
    if binary == "systemctl":
        verb, found = _systemctl_verb(prefix)
        if verb:
            return "mutation", f"systemctl {verb}", complete
        if not found:
            return "unresolved_argv", "systemctl <verb>", False
        return "exec", "systemctl", complete
    if binary == "ip":
        command, found = _ip_verb(prefix)
        if command:
            return "mutation", f"ip {command}", complete
        if not found:
            return "unresolved_argv", "ip <object>", False
        # A read. `exec` and not `host_reader`: `ip` is not one of §18's six
        # commands, and labelling it a host reader would put it in the set the
        # MCP-layer rule matches on — a fabrication in the other direction,
        # where a command §18 never named becomes one the MCP layer may not
        # run. The MCP layer may hold no host knowledge of its own either way;
        # that is enforced by the rule, not by a label it was never given.
        return "exec", "ip", complete
    if binary in ("pkill", "killall", "kill", "rm", "rmdir", "shred",
                  "shutdown", "reboot", "poweroff", "halt"):
        return "mutation", binary, complete
    return "exec", binary, complete


# The callee names whose first argument this scanner will report as an
# unreadable command even when it cannot resolve it. Without this set, every
# call whose first argument is not a literal would have to be reported and the
# report would be useless; with it, the handoff sites are the ones whose name
# says they run something, and a literal command handed to a callee NOT in this
# set is still caught by the literal check, which does not consult the name.
#
# This is a heuristic and it is written down as one. The alternative — guessing
# from the argument's shape instead — is the same guess with a better disguise.
HANDOFF_CALLEES = frozenset({
    "run", "_run", "read", "call", "execute", "exec", "spawn", "invoke",
    "popen", "shell", "check", "check_call", "check_output", "communicate",
})


def _assigned(stmt: ast.stmt) -> list[tuple[str, ast.AST]]:
    if isinstance(stmt, ast.Assign):
        return [(t.id, stmt.value) for t in stmt.targets if isinstance(t, ast.Name)]
    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        return ([(stmt.target.id, stmt.value)]
                if isinstance(stmt.target, ast.Name) else [])
    return []


def _scope_bindings(body: list[ast.stmt]) -> dict[str, list[ast.AST]]:
    """Name → every expression it is assigned in this scope.

    Descends into `if`/`else`/`try`/`with`/`for`/`while` bodies and stops at
    nested functions and classes, because a call site inside a branch reads the
    binding its branch assigned and a scanner that only read top-level
    statements would miss `Actuator.restart` — where `command` is bound inside
    the `if unit:` arm and inside the `elif` arm and reaches a runner either
    way. That is the product's one real actuator and its argv is the thing §26
    is about.

    More than one binding for a name is kept as more than one rather than
    collapsed to "last one wins". `command` here is `["systemctl", …]` on one
    arm and `["docker", …]` on the other; reporting whichever was parsed last
    would be a coin flip presented as a reading.
    """
    bindings: dict[str, list[ast.AST]] = {}

    def walk(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            for name, value in _assigned(stmt):
                bindings.setdefault(name, []).append(value)
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(stmt, ast.If):
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                walk(stmt.body)
                for handler in stmt.handlers:
                    walk(handler.body)
                walk(stmt.orelse)
                walk(stmt.finalbody)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                walk(stmt.body)
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                walk(stmt.body)
                walk(stmt.orelse)

    walk(body)
    return bindings


class _Scanner(ast.NodeVisitor):
    """One file, walked once, with the enclosing definition and its bindings.

    The enclosing symbol matters because it is what a declaration is keyed on.
    `runtime/daemon.py` has several methods that reach a runner; a declaration
    that named only the file would cover whichever one was added next.

    The binding stack matters for the same reason in the other direction:
    without it, `self.runner.run(command, 60.0)` is an unresolvable name and the
    product's one real actuator is invisible.
    """

    def __init__(self, relpath: str):
        self.relpath = relpath
        self.stack: list[str] = []
        self.scopes: list[dict[str, list[ast.AST]]] = []
        self.hits: list[dict] = []
        self._seen: set[tuple] = set()

    # -- scope tracking ----------------------------------------------------

    def visit_Module(self, node: ast.Module):
        self.scopes.append(_scope_bindings(node.body))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node):
        self.stack.append(node.name)
        self.scopes.append(_scope_bindings(node.body))
        self.generic_visit(node)
        self.scopes.pop()
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    @property
    def symbol(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _bindings_of(self, node: ast.AST) -> list[ast.AST]:
        """Every expression a Name could hold at this point, followed once.

        More than one result means the name is assigned on more than one branch
        of this scope and the scanner does not know which branch ran. It says
        so by returning both rather than by picking one.
        """
        if not isinstance(node, ast.Name):
            return [node]
        for scope in reversed(self.scopes):
            if node.id in scope:
                out: list[ast.AST] = []
                for bound in scope[node.id]:
                    if isinstance(bound, ast.Name):
                        out.extend(self._bindings_of(bound))
                    else:
                        out.append(bound)
                return out
        return [node]

    def _argv_of(self, node: ast.AST | None) -> tuple[list[str | None] | None,
                                                     tuple[str, ...]]:
        """(argv, candidate command words) for a call's first argument.

        `argv` is None when the shape is not a command or when the assignment
        is genuinely ambiguous. `candidates` names what the scanner DID see —
        `("systemctl", "docker")` for `Actuator.restart`'s `command` — because
        "two possible commands" is a fact a reviewer can act on and "unknown"
        is not.
        """
        if node is None:
            return None, ()
        bindings = self._bindings_of(node)
        parses = [a for a in (_static_argv(b) for b in bindings) if a]
        if not parses:
            return None, ()
        if len(parses) == 1:
            return parses[0], ()
        if len({p[0] for p in parses if p}) > 1:
            return None, tuple(p[0] for p in parses if p and p[0])
        first = [p for p in parses if p]
        return (first[0] if first else None), ()

    def _hit(self, node: ast.AST, kind: str, verb: str, detail: str = "",
             **extra) -> None:
        key = (getattr(node, "lineno", None), kind, verb)
        if key in self._seen:
            # One call site can be reached by two mechanisms (a `subprocess.run`
            # is both an exec call and a first-argument command). It is one
            # fact and it is reported once; the alternative is a report whose
            # counts depend on how many ways the scanner recognises a thing.
            return
        self._seen.add(key)
        self.hits.append({
            "file": self.relpath,
            "line": getattr(node, "lineno", None),
            "symbol": self.symbol,
            "kind": kind,
            "verb": verb,
            "detail": detail,
            **extra,
        })

    # -- calls -------------------------------------------------------------

    def visit_Call(self, node: ast.Call):
        dotted = _dotted(node.func)
        if dotted in MUTATING_OS_CALLS:
            self._hit(node, MUTATING_OS_CALLS[dotted], dotted)
            self.generic_visit(node)
            return

        if dotted in EXEC_CALLS:
            argv, candidates = (self._argv_of(node.args[0]) if node.args
                                else (None, ()))
            shell = any(kw.arg == SHELL_KWARG and _is_true(kw.value)
                        for kw in node.keywords)
            if argv is None:
                self._hit(node, "unresolved_argv",
                          "<%s>" % "|".join(candidates) if candidates else dotted,
                          _unresolved_why(candidates))
            else:
                kind, verb, complete = classify_argv(argv)
                self._hit(node, kind, verb,
                          "argv read from the source" if complete else
                          "argv partially read; the classification rested on "
                          "words the scanner could see", complete=complete)
            if shell:
                self._hit(node, "unresolved_argv", f"{dotted}(shell=True)",
                          "a shell string is a command this scanner cannot read")
            self.generic_visit(node)
            return

        attribute = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if attribute in UNAMBIGUOUS_METHODS:
            receiver = ast.unparse(node.func.value) if hasattr(ast, "unparse") else "?"
            self._hit(node, UNAMBIGUOUS_METHODS[attribute], f".{attribute}()",
                      f"receiver: {receiver}")
        elif attribute in ARITY_RESOLVED_METHODS and _arity_resolves_to_path(
                attribute, node):
            receiver = ast.unparse(node.func.value) if hasattr(ast, "unparse") else "?"
            self._hit(node, ARITY_RESOLVED_METHODS[attribute], f".{attribute}()",
                      f"receiver: {receiver}; resolved by the signature "
                      f"`{attribute}` has on pathlib.Path")
        elif dotted not in EXEC_CALLS and node.args:
            self._handoff(node, attribute)

        self.generic_visit(node)

    def _handoff(self, node: ast.Call, attribute: str | None) -> None:
        """A command handed to something that is not `subprocess`.

        `Actuator.restart` binds `["docker", "restart", container]` and calls
        `self.runner.run(command, 60.0)`. Nothing in the call says "subprocess"
        and the command never appears next to the word `exec` — which is
        exactly why a scan for subprocess call sites alone would report that
        the product has no actuator at all.
        """
        argv, candidates = self._argv_of(node.args[0])
        callee = _dotted(node.func) or "<call>"
        # No Python builtin runs a program, so a builtin callee cannot be a
        # command handoff. `set(HOST_READERS)` — the scanner's own module-level
        # vocabulary tuple, whose first member is `docker` — otherwise reads as
        # `docker df free ss du find` handed to `set`, and `raise
        # OSError("docker ps failed")` as a command handed to an exception. Both
        # are the name-based heuristic firing on a value that was never a
        # command; neither is fixed by making the heuristic longer.
        if isinstance(node.func, ast.Name) and node.func.id in _BUILTIN_NAMES:
            return
        # A command handed somewhere for execution is a sequence holding one. A
        # bare string at the same position is usually a predicate or a message —
        # `key.startswith("docker")` reads as a command to `_static_argv`, which
        # splits strings into words because `subprocess.run("docker ps",
        # shell=True)` really does run one. The distinction is the caller's
        # shape, so it is read from the argument rather than guessed from it.
        if not any(isinstance(b, (ast.List, ast.Tuple))
                   for b in self._bindings_of(node.args[0])):
            return
        if argv is None:
            # Reported only when the scanner found a name that IS bound to a
            # command somewhere in this scope. Without that condition the
            # callee-name heuristic alone reports `conn.execute`, `resp.read`,
            # `os.read` and `client.call` — every method that happens to share
            # a name with something that runs a command — and a table padded
            # with those is a table a reviewer stops reading.
            #
            # What is NOT lost by being this narrow: a command that arrives
            # through a chain of parameters still terminates at a literal in
            # this tree, and that literal is found at the call site that wrote
            # it. The blind spot this leaves is a command assembled outside the
            # repository entirely, which is `coverage_limits`' subject.
            if attribute in HANDOFF_CALLEES and candidates:
                self._hit(node, "unresolved_argv",
                          "<%s>" % "|".join(candidates),
                          f"handed to {callee}; " + _unresolved_why(candidates))
            return
        if not argv or argv[0] is None:
            return
        binary = Path(argv[0]).name
        if binary not in HANDOFF_BINARIES:
            return
        kind, verb, complete = classify_argv(argv)
        self._hit(node, "command_handoff", verb, f"handed to {callee}",
                  complete=complete, inner_kind=kind)


def _unresolved_why(candidates: tuple[str, ...]) -> str:
    if candidates:
        return ("the same name is assigned on more than one branch of this "
                "scope, to commands beginning "
                f"{', '.join(sorted(set(candidates)))}; which one runs is not "
                "decidable from here")
    return ("the first argument is a command this scanner cannot resolve; "
            "reported rather than passed over")


# The signature each arity-resolved name has on the type that mutates, and the
# signature it has on the type that does not. `Path.replace(target)` takes one
# positional argument; `str.replace(old, new[, count])` takes two or three and
# `datetime.replace(...)` is keyword-only. `Popen.kill()` takes none.
ARITY_RESOLVED_SIGNATURES = {
    "replace": {"path": (1, 0), "not_path": ((2, 3), "keywords")},
    "kill": {"path": (0, 0), "not_path": None},
}


def _arity_resolves_to_path(name: str, node: ast.Call) -> bool:
    """Whether a `.replace(...)`/`.kill(...)` is the pathlib/process one.

    Read off the argument list rather than guessed from the receiver's name.
    `receiver: tmp` is a temp file being renamed into place in one file and a
    `datetime` being normalised in another, and a scanner that keyed on the
    text would report one of them wrong no matter which way it guessed.
    """
    (args, keywords) = ARITY_RESOLVED_SIGNATURES[name]["path"]
    return len(node.args) == args and len(node.keywords) == keywords


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def scan_file(relpath: str, text: str) -> dict:
    """Every hit in one source, or a parse failure reported as one.

    A file this scanner cannot parse is not a file with no hits. Syntax the
    scanner cannot read is exactly as invisible as an argv it cannot read, and
    it is reported the same way.
    """
    try:
        tree = ast.parse(text, filename=relpath)
    except SyntaxError as exc:
        return {"file": relpath, "hits": [], "parsed": False,
                "error": f"SyntaxError: {exc}"}
    scanner = _Scanner(relpath)
    scanner.visit(tree)
    return {"file": relpath, "hits": scanner.hits, "parsed": True, "error": None}


def scan_sources(sources: dict[str, str]) -> dict:
    """Scan `{relpath: source}` and group the hits for a reader.

    The per-file results are kept, including the empty ones, so that "this
    file has no hits" and "this file was never scanned" cannot be confused —
    the second is the failure mode of every scanner that reports only what it
    found.
    """
    files = [scan_file(rel, text) for rel, text in sorted(sources.items())]
    hits = [hit for f in files for hit in f["hits"]]
    return {
        "schema": SCHEMA,
        "files_scanned": len(files),
        "files": files,
        "hits": hits,
        "by_kind": {kind: sum(1 for h in hits if h["kind"] == kind)
                    for kind in sorted({h["kind"] for h in hits})},
        "unparsed": [f["file"] for f in files if not f["parsed"]],
    }


# ---------------------------------------------------------------------------
# The declaration, and the two directions it is checked in
# ---------------------------------------------------------------------------


def load_actuators(path: Path) -> dict:
    """The declared actuator table. An unreadable table is not an empty one."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    return doc or {}


def _hit_key(hit: dict) -> tuple:
    return (hit["file"], hit["symbol"], hit["kind"], hit["verb"])


def audit(*, scan: dict, contract: dict) -> dict:
    """Check the scan and the declaration against each other, both ways.

    The two scopes are checked differently, and the difference is the
    contract's to state rather than this function's to assume. A product call
    site is declared one at a time, because every one of them is a permission.
    A test file is inventoried but not site-declared — a hundred fixture
    teardowns declared by name is a table nobody reads, which is the same
    failure as no table — so instead the contract names the kinds a test may
    not reach at all, and that check is applied to every test hit.
    """
    declared = contract.get("actuators") or []
    declared_keys: dict[tuple, dict] = {}
    for a in declared:
        for key in _cover_keys(a):
            declared_keys.setdefault(key, a)

    product = [h for h in scan["hits"] if _scope_of(h) == "product"]
    tests = [h for h in scan["hits"] if _scope_of(h) == "tests"]

    undeclared = [h for h in product if _hit_key(h) not in declared_keys]

    hit_keys = {_hit_key(h) for h in product}
    stale = []
    for a in declared:
        keys = _cover_keys(a)
        if not keys:
            stale.append({"id": a.get("id"), "expected": [],
                          "why": "the declaration names no site, so it covers "
                                 "nothing and cannot be violated by anything"})
            continue
        if not any(k in hit_keys for k in keys):
            # Unless the actuator declares it is allowed to match nothing —
            # the same distinction `required: false` draws in the state-root
            # contract, and for the same reason: an absence that is declared
            # is a fact, an absence nobody declared is a discovery.
            if not a.get("may_be_absent"):
                stale.append({"id": a.get("id"), "expected": keys})

    # §26's scope rule, read from the contract rather than held in this file.
    tests_policy = _scope_policy(contract, "tests")
    tests_forbidden = set(tests_policy.get("forbidden_kinds") or [])
    tests_breach = [h for h in tests if h["kind"] in tests_forbidden]

    # §18's layer rule, absolute and not subject to the exemption table: the
    # MCP process runs no host command at all. It is stated against the
    # contract's list, and the list itself is checked against the scanner's
    # vocabulary — a `host_reader` the contract forgot to forbid would otherwise
    # be a hole in the rule that nothing reports.
    mcp_forbidden = set(contract.get("mcp_layer_forbidden_commands") or [])
    mcp_layer = [h for h in scan["hits"]
                 if _in_mcp_layer(h) and h["kind"] == "host_reader"]
    mcp_list_drift = sorted(mcp_forbidden ^ set(HOST_READERS))

    return {
        "schema": schema_of(contract),
        "declared": [{"id": a.get("id"), "kind": a.get("kind"),
                      "verb": a.get("verb"), "why": a.get("why"),
                      "sites": a.get("sites") or [],
                      "keys": _cover_keys(a)} for a in declared],
        "undeclared": undeclared,
        "stale": stale,
        "tests": tests,
        "tests_breach": tests_breach,
        "mcp_layer_host_reach": mcp_layer,
        "mcp_layer_list_drift": mcp_list_drift,
        "scanned_files": scan["files_scanned"],
        "unparsed_files": scan["unparsed"],
        "counts": {
            "hits": len(scan["hits"]),
            "product_hits": len(product),
            "test_hits": len(tests),
            "declared": len(declared),
            "undeclared": len(undeclared),
            "stale": len(stale),
            "tests_breach": len(tests_breach),
            "mcp_layer_host_reach": len(mcp_layer),
        },
    }


def _scope_policy(contract: dict, scope: str) -> dict:
    for row in contract.get("scopes") or []:
        if row.get("scope") == scope:
            return row
    return {}


def schema_of(contract: dict) -> str:
    return contract.get("schema") or SCHEMA


def _cover_keys(a: dict) -> list[tuple]:
    """The `(file, symbol, kind, verb)` keys one declaration covers.

    A declaration is written as a **capability** with a list of `sites`, and
    that is not decoration: `host-reader-commands` is one permission granted to
    eleven methods across two files, and splitting it into eleven rows would
    make a reviewer reassemble it before they could answer "may the daemon run
    docker?". One capability, one row, one reason.

    `kind` and `verb` are declared once and may be **overridden per site**,
    because the reachability genuinely varies inside a capability. `probe-commands`
    names three probes that run three different binaries; `atomic-write-commit`
    covers `os.replace(tmp, path)` and `Path.replace(target)` at the same call
    sites. The alternative — splitting the entry — loses the grouping, and the
    alternative to both — leaving `verb` out of the key — would let a
    declaration say `git` about code that says `rm`.

    `symbols: ["*"]` means the whole file, written out rather than defaulted
    off an absent key: a field that silently widens scope to a file is how an
    exemption grows.
    """
    keys: list[tuple] = []
    for site in a.get("sites") or []:
        for symbol in site.get("symbols") or []:
            keys.append((site.get("file"), symbol,
                         site.get("kind", a.get("kind")),
                         site.get("verb", a.get("verb"))))
    return keys


def _in_mcp_layer(hit: dict) -> bool:
    return hit["file"].startswith("mcp/")


def _scope_of(hit: dict) -> str:
    """`product` or `tests`, decided by where the file lives and nothing else."""
    return "tests" if hit["file"].startswith(TESTS_PREFIX) else "product"


TESTS_PREFIX = "tests/"


# ---------------------------------------------------------------------------
# §18 · the producer closure
# ---------------------------------------------------------------------------


def resolve_producers(contract: dict, *, exists) -> dict:
    """Every namespace's producer, resolved or reported as unresolved.

    A producer that is a path is checked for existence. A producer that is a
    sentence names a domain outside this repository — `workspace-management`,
    `ability-management` — and is recorded as `external` rather than failed:
    this checker cannot verify another repository from here, and inventing a
    verdict for one it cannot see is the failure this reports.

    What it will NOT accept is a sentence that names nothing. The two are told
    apart by whether the string names a known external domain, listed in the
    contract itself, so adding one is a decision rather than a typo that
    quietly becomes a pass.
    """
    external = set(contract.get("external_producers") or [])
    rows = []

    def walk(node, prefix=""):
        for name, spec in (node or {}).items():
            if not isinstance(spec, dict):
                continue
            ref = f"{prefix}{name}"
            producer = spec.get("producer")
            if producer:
                rows.append(_producer_row(ref, producer, exists, external,
                                          contract))
            walk(spec.get("children"), ref + "/")

    walk(contract.get("namespaces"))
    unresolved = [r for r in rows if r["resolution"] == "UNRESOLVED"]
    return {
        "namespaces": rows,
        "unresolved": unresolved,
        "external": [r["namespace"] for r in rows if r["resolution"] == "external"],
        "counts": {"declared": len(rows), "unresolved": len(unresolved)},
    }


def _producer_row(ref: str, producer: str, exists, external: set,
                  contract: dict) -> dict:
    """Where one namespace's facts come from, resolved or reported.

    The order of the three readings is the whole content of this function, and
    it is not the order the strings are written in:

      1. an **external domain** the contract has listed — asked first, because a
         domain is a bare word (`workspace-management`) that the path reading
         would otherwise mistake for a relative directory and report as absent.
         A skill that lives in another tree reported as a missing path is a
         false accusation, and a contract that makes false accusations stops
         being read;
      2. a **path in this tree**, which is the only reading that can be checked
         from here;
      3. a **pointer into this contract** (`projections.manifest`), for a fact
         whose producer is defined by another declaration in the same document.

    Anything else is prose, and prose that names no path, no declared domain
    and no contract section is `UNRESOLVED`: a producer nobody can resolve is a
    producer nobody can be missing.
    """
    text = producer.strip()
    named = sorted(domain for domain in external if domain in text)
    if named:
        return {"namespace": ref, "producer": text, "resolution": "external",
                "named_domains": named,
                "why": ("names an external domain listed in external_producers; "
                        "this checker cannot read it from here and says so")}

    if " " not in text and not text.endswith("#"):
        if _contract_lookup(contract, text) is not None:
            return {"namespace": ref, "producer": text,
                    "resolution": "CONTRACT", "named_domains": [],
                    "why": ("is a section of this same contract, so the "
                            "producer is defined by the declaration rather "
                            "than by a file this checker could open")}
        if exists(text.split("#")[0].strip()):
            return {"namespace": ref, "producer": text, "resolution": "PRESENT",
                    "named_domains": [], "why": None}
        return {"namespace": ref, "producer": text, "resolution": "MISSING",
                "named_domains": [],
                "why": (f"names {text!r}, which is not in the tree and is not a "
                        f"declared external domain. A namespace whose producer "
                        f"does not exist is not unwritten — it is unwritable, "
                        f"and its absence would be reported forever as the "
                        f"wrong thing")}
    return {"namespace": ref, "producer": text, "resolution": "UNRESOLVED",
            "named_domains": [],
            "why": ("names no path, no declared external domain and no section "
                    "of this contract, so nothing can check it. Either the "
                    "producer exists somewhere this contract has not named, or "
                    "the fact has no producer at all — and those two are told "
                    "apart by somebody saying which, not by this checker "
                    "guessing")}


def _contract_lookup(contract: dict, dotted: str):
    node = contract
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# The findings, in the shape architecture_check consumes
# ---------------------------------------------------------------------------


def findings(*, audit_report: dict, producer_report: dict) -> list[dict]:
    """Findings for the architecture gate, with the severity §18/§26 warrant.

    HIGH is for the rows where the code does something nobody reviewed: a call
    site with no declaration, the MCP layer holding host knowledge, a source
    that could not be parsed, a test running a state-changing verb, and a
    producer that named a path which is not there.

    MEDIUM is for the rows that say a record has drifted from the world rather
    than that the world is wrong: a permission whose code is gone, a policy
    list out of step with the vocabulary it is a policy about, and a producer
    written as a sentence that names nothing. That last one is the calibration
    that matters — the fact may well have a real producer and nobody wrote down
    which, which is a documentation defect, while `PRODUCER_ABSENT` is the
    stronger statement that the namespace cannot be written at all. Rating both
    HIGH would make this checker's first honest run fail a gate that was green
    before it existed, and a gate that goes red when it is first switched on is
    a gate that gets switched off.
    """
    out: list[dict] = []

    if audit_report["undeclared"]:
        out.append({
            "id": "UNDECLARED_ACTUATOR", "class": "safety",
            "desired": ("every call site that can change something outside this "
                        "process is declared in runtime/actuator-contract.yaml "
                        "with a reason"),
            "observed": audit_report["undeclared"],
            "authority": "§26 — no code path may destroy a resource this round",
            "severity": "HIGH",
        })

    if audit_report["stale"]:
        out.append({
            "id": "STALE_ACTUATOR", "class": "safety",
            "desired": "every declared actuator still matches a call site",
            "observed": audit_report["stale"],
            "authority": ("§26 by way of §18 — a permission that outlived the "
                          "code it was granted for is the same defect as a "
                          "declaration nothing emits"),
            "severity": "MEDIUM",
        })

    if audit_report["mcp_layer_host_reach"]:
        out.append({
            "id": "MCP_LAYER_REACHES_HOST", "class": "architecture",
            "desired": ("the MCP layer runs no host command: serverosd is the "
                        "producer of every host fact and the MCP process "
                        "transcribes it over the daemon socket"),
            "observed": audit_report["mcp_layer_host_reach"],
            "authority": "§18 — one producer per fact",
            "severity": "HIGH",
        })

    if audit_report["mcp_layer_list_drift"]:
        out.append({
            "id": "MCP_FORBIDDEN_LIST_DRIFT", "class": "architecture",
            "desired": ("the list of commands the MCP layer may not run is the "
                        "list of commands this scanner calls a host read"),
            "observed": audit_report["mcp_layer_list_drift"],
            "authority": ("§18 — a forbidden list that lags the vocabulary is "
                          "the §18 hole with a policy around it"),
            "severity": "MEDIUM",
        })

    if audit_report["tests_breach"]:
        out.append({
            "id": "TEST_REACHES_A_MUTATING_COMMAND", "class": "safety",
            "desired": ("no test invokes a command that changes state, whatever "
                        "teardown it believes it is doing"),
            "observed": audit_report["tests_breach"],
            "authority": ("§26 — the fixture-teardown exemption covers removing "
                          "what the test itself created, and it is not a licence "
                          "to run a state-changing command"),
            "severity": "HIGH",
        })

    if audit_report["unparsed_files"]:
        out.append({
            "id": "SOURCE_UNPARSEABLE", "class": "safety",
            "desired": "every scanned source parses, so a hit cannot hide in it",
            "observed": audit_report["unparsed_files"],
            "authority": "§26 — an unscanned file is not a clean file",
            "severity": "HIGH",
        })

    if producer_report["unresolved"]:
        out.append({
            "id": "PRODUCER_UNRESOLVABLE", "class": "architecture",
            "desired": ("every declared namespace producer is a path in the "
                        "tree, an external domain named in the contract, or a "
                        "section of the contract itself"),
            "observed": producer_report["unresolved"],
            "authority": ("§18 — one fact, one producer. A producer that "
                          "resolves to nothing is the declaration-layer "
                          "pathology on the ownership surface"),
            # MEDIUM, and the calibration is deliberate. A producer written as
            # a sentence that names nothing is a documentation defect: the fact
            # may well have a producer, and nobody wrote down which. A producer
            # that names a PATH which is not there (PRODUCER_ABSENT, below) is
            # a different and worse statement — the file was named and does not
            # exist — and that one is HIGH. Rating both HIGH would make the
            # first honest run of this checker fail a gate that was green
            # before it existed, which is how a gate gets turned off.
            "severity": "MEDIUM",
        })

    for row in producer_report["namespaces"]:
        if row["resolution"] == "MISSING":
            out.append({
                "id": "PRODUCER_ABSENT", "class": "architecture",
                "subject": row["namespace"],
                "desired": f"the producer {row['producer']!r} exists",
                "observed": row["why"],
                "authority": "§18 — a namespace whose producer is missing",
                "severity": "HIGH",
            })

    return out


def self_scan(*, root: Path) -> dict:
    """Scan this file. A checker that exempts itself is not a checker."""
    return scan_sources({"skills/serveros/scripts/mutation_guard.py":
                         (root / "skills/serveros/scripts/mutation_guard.py").read_text()})


def check(*, root: Path, actuators: dict, state_root: dict,
          exists=None) -> dict:
    """Both halves of §18 in one report: what the code can reach, and who
    produces what the code claims to own.

    The declarations arrive **already read** rather than as paths, and that is
    not a convenience. `architecture_check.py` reads every declaration out of a
    pinned git object so that a gate cannot be satisfied by an uncommitted edit
    to the contract it is checking. A function that took a path and opened it
    would hand that caller a working-tree read wearing the same name, so the
    two halves are separated here: the caller supplies the declarations it
    vouches for, and this function supplies the scan.

    They are reported together because they are one question asked of two
    layers — the reachability surface and the ownership surface — and a caller
    that had to invoke two binaries would eventually invoke one.
    """
    sources = {str(p.relative_to(root)): p.read_text(errors="replace")
               for p in sorted(root.rglob("*.py"))
               if ".git" not in p.parts and "node_modules" not in p.parts}
    scan = scan_sources(sources)
    audit_report = audit(scan=scan, contract=actuators)
    producer_report = resolve_producers(
        state_root,
        exists=exists or (lambda p: (root / p).exists()))

    rows = findings(audit_report=audit_report, producer_report=producer_report)
    return {
        "schema": SCHEMA,
        "status": ("FAILED" if any(f["severity"] == "HIGH" for f in rows)
                   else "CHECKED"),
        "scanned_from": str(root),
        "audit": audit_report,
        "producers": producer_report,
        "findings": rows,
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd(),
                    help="the tree to scan; declarations are read from under "
                         "it too, which is what the CLI is for")
    ap.add_argument("--contract", type=Path,
                    default=Path("runtime/actuator-contract.yaml"))
    ap.add_argument("--state-root-contract", type=Path,
                    default=Path("fleet/state-root-contract.yaml"))
    ap.add_argument("--json", action="store_true",
                    help="the full report; this checker emits JSON either way, "
                         "so a caller that forgets the flag is not silently "
                         "handed nothing")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    report = check(root=root,
                   actuators=load_actuators(root / args.contract),
                   state_root=load_actuators(root / args.state_root_contract))
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
