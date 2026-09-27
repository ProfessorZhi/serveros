#!/usr/bin/env python3
"""Backup and restore for a node's OpenViking workspace.

    python3 backup.py backup  --workspace DIR --out FILE
    python3 backup.py verify  --archive FILE
    python3 backup.py restore --archive FILE --into DIR

WHY THIS IS A FILE AND NOT A SENTENCE IN A RUNBOOK
--------------------------------------------------
`memory/provider-contract.yaml` asks of a Layer C provider that it be
rebuildable, and it asks of this one that its store be backed up. "Run tar" is
a sentence, and a sentence cannot fail — so nothing would notice that the
archive it produced omitted the vector index, or that the restore silently
skipped a file it could not read. This script is the thing that fails.

WHAT IT IS ACTUALLY CHECKING
----------------------------
The workspace is a DIRECTORY OF FILES whose meaning is in the bytes, so a
complete backup is a manifest of every entry's identity, taken at one instant,
plus the bytes. `backup` writes both and `verify` recomputes the identities
from the archive's own stream — the same discipline `release.fingerprint`
applies to an artifact, for the same reason: a receipt that is not recomputed
is a receipt that cannot disagree with anything.

Every entry the workspace holds is in the manifest, including the ones that
are not regular files. A symlink is recorded as its target and recreated as a
link, not followed and not skipped; a mode is recorded and re-applied, so a
store whose lock file is `0600` does not come back `0644`. Both of those are
here because the alternative is not "less detail" — it is an archive that
verifies cleanly while having lost something, which is the single failure this
file exists to make impossible.

WHAT IT REFUSES
---------------
A store that is being written while it is copied is a store whose manifest and
whose bytes need not describe the same instant, so `backup` refuses when
something answers on the provider's port and says so, rather than producing an
archive that verifies cleanly and is wrong. `--allow-live` exists because an
operator may have a reason; it records `live: true` in the manifest so a later
reader is told which kind of archive they hold.

`restore` NEVER MERGES. It extracts into a target that must be empty, so a
restore cannot become a second, half-updated copy of a store — the failure
this repository keeps finding in other forms.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "serveros/openviking-backup.v1"
MANIFEST_NAME = "MANIFEST.json"
CHUNK = 1024 * 1024
MEMBER_ROOT = "workspace"


def _sha256_stream(fh) -> str:
    digest = hashlib.sha256()
    while True:
        block = fh.read(CHUNK)
        if not block:
            return digest.hexdigest()
        digest.update(block)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as fh:
        return _sha256_stream(fh)


def _entries(root: Path) -> list[dict]:
    """Identity for every entry, by kind.

    A regular file is its bytes and its mode. A symlink is its target. A
    directory is not an entry: the archive carries the directory structure
    implicitly, and a mode on a directory that `restore` re-applies would be
    the one place a recorded mode could surprise a target filesystem.
    """
    out = []
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out.append({"path": rel, "type": "link",
                        "target": os.readlink(path), "mode": path.lstat().st_mode & 0o7777})
        elif path.is_file():
            out.append({"path": rel, "type": "file", "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                        "mode": path.stat().st_mode & 0o7777})
        elif path.is_dir():
            continue
        else:
            # A fifo, socket or device in a memory store is a fact an operator
            # has to decide about; silently leaving it out of the manifest is
            # the one thing this function must not do.
            raise SystemExit(f"REFUSED {path} is neither a file, a directory nor "
                             "a symlink, and this script will not back up a store "
                             "by omitting part of it")
    return out


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _safe_member(name: str) -> str:
    """A member path that cannot leave the directory it is extracted into.

    `tarfile.extractall` refuses absolute paths on its own only in recent
    Pythons, and the `..` half is the one that survives a filter. Both are
    checked here so the answer does not depend on the interpreter's version.
    """
    pure = Path(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"archive member escapes the target directory: {name!r}")
    return name


def _inside(root: Path, dest: Path) -> Path:
    """The check above, applied to the path that will actually be written.

    This is the assertion that `restore` acts on — not the member name, which
    is only how the path was spelled. Kept separate from `_safe_member` because
    the first draft of this file passed an absolute destination to the member
    check and made `restore` refuse to restore anything; the two questions look
    alike and are not.
    """
    real_root, real_dest = os.path.realpath(root), os.path.realpath(dest)
    if real_dest != real_root and not real_dest.startswith(real_root + os.sep):
        raise ValueError(f"refusing to write {dest} outside {root}")
    return dest


def backup(workspace: Path, out: Path, *, port: int | None, allow_live: bool) -> dict:
    if not workspace.is_dir():
        raise SystemExit(f"REFUSED {workspace} is not a directory")
    live = bool(port) and _listening(port)
    if live and not allow_live:
        raise SystemExit(
            f"REFUSED something answers on 127.0.0.1:{port}, so the store is "
            "being written while it is copied and the manifest and the bytes "
            "need not describe the same instant.\n"
            f"          Stop it first:  systemctl --user stop openviking\n"
            f"          Or record what you are doing:  --allow-live")
    entries = _entries(workspace)
    manifest = {"schema": SCHEMA, "workspace": str(workspace),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "live": live, "entries": entries,
                "files": sum(1 for e in entries if e["type"] == "file"),
                "links": sum(1 for e in entries if e["type"] == "link"),
                "bytes": sum(e.get("size", 0) for e in entries)}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(workspace, arcname=MEMBER_ROOT, recursive=True)
        blob = json.dumps(manifest, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(blob)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(blob))
    tmp.replace(out)
    return {"archive": str(out), **{k: manifest[k] for k in
                                    ("files", "links", "bytes", "live", "created_at")}}


def _manifest_of(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(MANIFEST_NAME)
        except KeyError:
            raise SystemExit(f"REFUSED {archive} carries no {MANIFEST_NAME}, so "
                             "it was not written by this script and there is "
                             "nothing to check it against")
        return json.loads(tar.extractfile(member).read().decode())


def verify(archive: Path, *, into: Path | None = None) -> dict:
    """Check the archive against its own manifest, optionally restoring as it goes.

    One pass does both because the two answers must come from the same read of
    the same bytes: a restore that reported success from a second pass could be
    a second pass over a different archive.
    """
    manifest = _manifest_of(archive)
    expected = {e["path"]: e for e in manifest["entries"]}
    seen, bad = set(), []
    if into is not None:
        into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name == MANIFEST_NAME:
                continue
            try:
                _safe_member(member.name)
            except ValueError as exc:
                # A traversal attempt is not a damaged archive to be reported
                # alongside the rest; it is a reason to stop. Reported as a
                # refusal so the exit is a statement rather than a traceback.
                raise SystemExit(f"REFUSED {archive}: {exc}")
            rel = (member.name[len(MEMBER_ROOT) + 1:]
                   if member.name.startswith(MEMBER_ROOT + "/") else member.name)
            if not rel or member.isdir():
                # Directories carry no identity of their own here: the archive
                # records the structure, and the manifest records leaves. A
                # directory member is therefore expected and not an entry, which
                # is why it is skipped BEFORE the manifest lookup rather than
                # reported as a file the manifest never named.
                continue
            want = expected.get(rel)
            if want is None:
                bad.append({"path": rel, "why": "not named by the manifest"})
                continue
            want_kind = want["type"]

            if want_kind == "link":
                if not member.issym() or member.linkname != want["target"]:
                    bad.append({"path": rel,
                                "why": f"link {member.linkname!r} != {want['target']!r}"})
                    continue
                if into is not None:
                    dest = _inside(into, into / rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(want["target"], dest)
                seen.add(rel)
                continue

            if not member.isfile():
                bad.append({"path": rel, "why": f"manifest says file, archive has "
                                                f"{'link' if member.issym() else 'non-file'}"})
                continue
            fh = tar.extractfile(member)
            if fh is None:
                bad.append({"path": rel, "why": "unreadable in archive"})
                continue
            if into is not None:
                dest = _inside(into, into / rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as out_fh:
                    while True:
                        block = fh.read(CHUNK)
                        if not block:
                            break
                        out_fh.write(block)
                if "mode" in want:
                    os.chmod(dest, want["mode"])
                fh.seek(0)
            got = _sha256_stream(fh)
            if got != want["sha256"] or member.size != want["size"]:
                bad.append({"path": rel, "why": f"sha256 {got[:12]} != {want['sha256'][:12]}"})
            else:
                seen.add(rel)
    missing = sorted(set(expected) - seen)
    return {"schema": SCHEMA, "archive": str(archive), "live": manifest["live"],
            "created_at": manifest["created_at"],
            "expected": len(expected), "checked": len(seen),
            "files": manifest.get("files"), "links": manifest.get("links"),
            "missing": missing, "mismatched": bad,
            "ok": not missing and not bad}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)

    b = sub.add_parser("backup")
    b.add_argument("--workspace", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--port", type=int, default=1933,
                   help="liveness probe; 0 disables it")
    b.add_argument("--allow-live", action="store_true")

    v = sub.add_parser("verify")
    v.add_argument("--archive", required=True)

    r = sub.add_parser("restore")
    r.add_argument("--archive", required=True)
    r.add_argument("--into", required=True)

    args = parser.parse_args(argv)
    if args.verb == "backup":
        result = backup(Path(args.workspace), Path(args.out),
                        port=args.port or None, allow_live=args.allow_live)
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if args.verb == "verify":
        result = verify(Path(args.archive))
    else:
        into = Path(args.into)
        occupants = [p for p in into.iterdir()] if into.is_dir() else []
        if occupants:
            raise SystemExit(f"REFUSED {into} is not empty. A restore over a "
                             "populated directory is a merge, and a half-updated "
                             "store is worse than no store.")
        result = verify(Path(args.archive), into=into)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
