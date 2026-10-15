#!/usr/bin/env python3
"""Build the PRIVATE App-backed overlay ZIP for the installed Fleet plugin.

This is a different product from `integrations/chatgpt-fleet-next/build_zip.py`,
and the difference is the whole reason it is a separate file rather than a flag
on that one.

| | `chatgpt-fleet-next/build_zip.py` | this |
|---|---|---|
| what it produces | an instruction-only plugin | an **overlay** for an already-installed private plugin |
| may contain `apps` / `.app.json` | **no — refused** | **yes, and the reference must not change** |
| may contain `.codex-plugin/` | **no — refused** | yes; it is the compatibility manifest |
| where it may go | a public submission | **private Plugin Creator update only** |

That table is drawn from the official documentation read on 2026-10-15
(`developers.openai.com/plugins/build/plugins` and `.../deploy/submission`),
which states plainly that a ZIP containing app references or lifecycle hooks
"cannot currently be submitted" publicly. So the two builders are mutually
exclusive by construction, and this one is marked as private at every step it
can be: in the archive's own release record, in its stdout, and in a refusal if
anyone points it at a source tree carrying a credential.

What this builder will NOT do
-----------------------------
* It will not create a plugin. There is no create path here; the only supported
  update is against a plugin that already exists, guarded by the release id the
  operator read back from the Creator.
* It will not change the plugin's identity. The id, the name, the `displayName`
  and the `apps` reference are pinned to a baseline file and a source that moves
  any of them is refused. Changing the app binding is not an overlay; it is a
  different plugin wearing this one's release slot.
* It will not put a credential, a token or a private key in the archive, and it
  will not read one either.
* It will not talk to anything. No network, no Creator, no OAuth.

Icon rules are OURS, not the platform's
---------------------------------------
The official page gives **no** format, dimension or file-size limit for
`composerIcon`, `logo` or `screenshots`. The checks below (PNG/JPEG/WebP/SVG,
at least 48x48, at most 5 MiB) are this repository's own release rules, chosen
so an unusable asset fails here instead of in a review queue. They are not a
conformance claim, and passing them says nothing about how — or whether — a
client renders the image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zipfile
from pathlib import Path

#: Where the OpenAI extension lives in the manifest, verbatim from the docs.
OPENAI_NS = "com.openai"

#: The package layout the docs fix: these stay at the plugin root.
ROOT_MEMBERS = ("plugin.json", "mcp.json", "skills", "assets")

#: This repository's own icon rules. See the module docstring: the platform
#: documents none of them.
ICON_FORMATS = ("png", "jpg", "jpeg", "webp", "svg")
ICON_MIN_PIXELS = 48
ICON_MAX_BYTES = 5 * 1024 * 1024

#: Never pack these. A public or private ZIP is a thing that gets uploaded and
#: mirrored; a repository-internal file that reaches it has left the building.
FORBIDDEN_PARTS = frozenset({
    ".git", ".env", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", "dist", "out", "tests",
})
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".pyc", ".pyo")
FORBIDDEN_NAMES = frozenset({
    ".env", "credentials.json", "token.json", "id_rsa", "id_ed25519",
    "test_credentials.json", "reviewer_instructions.json",
})

#: A fixed timestamp, not "now": a build that stamps the wall clock produces a
#: different archive every second and cannot be verified by rebuilding.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = 0o644 << 16

SEMVER_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")

#: The one thing this archive is for. Emitted so a file on disk answers the
#: question "may I upload this publicly?" without anyone having to remember.
DISTRIBUTION = "PRIVATE_APP_BACKED_OVERLAY_ONLY"


class Refused(Exception):
    """The source is not buildable. Carries the reason, not a traceback."""


def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise Refused(f"no {what} at {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Refused(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise Refused(f"{what} must be a JSON object")
    return value


def _openai_extension(manifest: dict) -> dict:
    extensions = manifest.get("extensions")
    if not isinstance(extensions, dict) or OPENAI_NS not in extensions:
        raise Refused(
            f"plugin.json declares no `extensions.{OPENAI_NS}`. The OpenAI "
            f"presentation block is where `displayName`, `composerIcon` and the "
            f"`apps` reference live; an overlay without it is the wrong manifest.")
    block = extensions[OPENAI_NS]
    if not isinstance(block, dict):
        raise Refused(f"`extensions.{OPENAI_NS}` must be an object")
    return block


def _semver(value: object, what: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER_RE.match(value):
        raise Refused(f"{what} {value!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(p) for p in SEMVER_RE.match(value).groups())  # type: ignore[return-value]


class Overlay:
    """An overlay source tree, validated against a baseline."""

    def __init__(self, root: Path, baseline: dict):
        self.root = root.resolve()
        self.baseline = baseline
        self.manifest_path = self.root / "plugin.json"
        self.manifest = _read_json(self.manifest_path, "plugin.json")
        self.compat_path = self.root / ".codex-plugin" / "plugin.json"
        self.compat = _read_json(self.compat_path, ".codex-plugin/plugin.json")
        self.openai = _openai_extension(self.manifest)

        self.plugin_id = baseline["plugin_id"]
        self.version = self.manifest.get("version")
        self.apps = self.openai.get("apps")

    # -- identity ----------------------------------------------------------
    def check_identity(self) -> list[str]:
        """The identity an overlay may not move, checked against the baseline."""
        notes = []
        pairs = (
            ("name", self.manifest.get("name"), self.baseline.get("name")),
            ("extensions.com.openai.displayName",
             self.openai.get("displayName"), self.baseline.get("displayName")),
            ("extensions.com.openai.apps", self.apps, self.baseline.get("apps")),
            (".codex-plugin interface.displayName",
             (self.compat.get("interface") or {}).get("displayName"),
             self.baseline.get("compatDisplayName")),
        )
        for label, found, pinned in pairs:
            if pinned is None:
                continue
            if found != pinned:
                raise Refused(
                    f"{label} is {found!r}; the baseline pins {pinned!r}. An "
                    f"overlay updates skills, assets and the version. Changing "
                    f"{label} is a different plugin, not an update to this one.")
            notes.append(f"{label} unchanged: {found!r}")
        return notes

    def check_version(self) -> str:
        new = _semver(self.version, "version")
        old = _semver(self.baseline.get("version"), "baseline version")
        if new <= old:
            raise Refused(
                f"version {self.version!r} does not advance on the pinned "
                f"{self.baseline.get('version')!r}. An update that does not "
                f"version cannot be told from the release it replaces.")
        return f"version advances {self.baseline['version']} -> {self.version}"

    def check_app_binding(self) -> str:
        """The app mapping must be present, must point at `.app.json`, and the
        file it names must be there — an overlay that dropped it would upload a
        plugin whose app is no longer bound."""
        if not isinstance(self.apps, str) or not self.apps.startswith("./"):
            raise Refused(
                f"`apps` is {self.apps!r}; it must be a root-relative `./` path "
                f"(the documentation's example is `./.app.json`)")
        target = (self.root / self.apps[2:]).resolve()
        if not str(target).startswith(str(self.root)):
            raise Refused(f"`apps` ({self.apps!r}) escapes the plugin root")
        if not target.is_file():
            raise Refused(
                f"`apps` names {self.apps!r} but that file is not in the source. "
                f"The binding the operator registered is not ours to omit.")
        return f"app binding preserved: {self.apps} -> {self.plugin_id}"


class AssetReport(dict):
    """A verification result that is never a claim about rendering."""

    @property
    def ok(self) -> bool:
        return self.get("status") == "OK"


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + seg
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 ":
        return struct.unpack("<HH", data[26:30])
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


_SVG_DIM = re.compile(rb'\b(width|height)\s*=\s*"([0-9]+)(?:px)?"')


def _svg_size(data: bytes) -> tuple[int, int] | None:
    if b"<svg" not in data[:2048]:
        return None
    found = {k.decode(): int(v) for k, v in _SVG_DIM.findall(data[:4096])}
    if "width" in found and "height" in found:
        return found["width"], found["height"]
    return None


_READERS = {"png": _png_size, "jpg": _jpeg_size, "jpeg": _jpeg_size,
            "webp": _webp_size, "svg": _svg_size}


def inspect_icon(path: Path, *, require_square: bool = False) -> AssetReport:
    """Validate one icon file against this repository's own rules.

    Returns a report, never a boolean: `status` is `OK`, `NEEDS_ASSET` or
    `REFUSED`, and every outcome carries the reason. `NEEDS_ASSET` is what an
    absent asset produces — the honest answer when the operator has not supplied
    a file, and not a build failure they cannot act on.

    `require_square` is set for `composerIcon` and not for `logo`, and the
    asymmetry is deliberate: a composer avatar is drawn into a square slot,
    while a logo is very often a wordmark whose whole point is its aspect ratio.
    Both the square requirement and the size/format limits are this repository's
    own release rules — the platform documents none of them — so they are stated
    as ours and never as a conformance claim.
    """
    if not path.is_file():
        return AssetReport(status="NEEDS_ASSET", slot=str(path.name),
                           reason="no file at this slot")
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in ICON_FORMATS:
        return AssetReport(status="REFUSED", slot=path.name,
                           reason=f"{suffix!r} is not one of {list(ICON_FORMATS)}")
    data = path.read_bytes()
    if len(data) > ICON_MAX_BYTES:
        return AssetReport(status="REFUSED", slot=path.name,
                           reason=f"{len(data)} bytes exceeds {ICON_MAX_BYTES}")
    size = _READERS[suffix](data)
    if size is None:
        return AssetReport(status="REFUSED", slot=path.name,
                           reason="dimensions could not be read from the file")
    width, height = size
    if width < ICON_MIN_PIXELS or height < ICON_MIN_PIXELS:
        return AssetReport(status="REFUSED", slot=path.name,
                           reason=f"{width}x{height} is below "
                                  f"{ICON_MIN_PIXELS}x{ICON_MIN_PIXELS}")
    if require_square and width != height:
        return AssetReport(status="REFUSED", slot=path.name,
                           reason=f"{width}x{height} is not square; a composer "
                                  f"avatar is drawn into a square slot")
    return AssetReport(status="OK", slot=path.name, format=suffix,
                       width=width, height=height, bytes=len(data),
                       square=width == height,
                       renders_as=None,
                       note="dimensions verified; how a client renders this is "
                            "not verified here")


def _resolve_asset(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.startswith("./"):
        return None
    return (root / value[2:]).resolve()


def audit(root: Path) -> None:
    """Refuse anything that must not leave this machine."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if set(rel.parts) & FORBIDDEN_PARTS:
            raise Refused(f"refusing to package {rel.as_posix()}: "
                          f"a name in that path never belongs in a ZIP")
        if path.is_symlink():
            raise Refused(f"refusing to package the symlink {rel.as_posix()}")
        if path.is_file():
            if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
                raise Refused(f"refusing to package {rel.as_posix()}: "
                              f"credential-shaped or by-product name")


def build(root: Path, out_dir: Path, baseline: dict) -> dict:
    audit(root)
    overlay = Overlay(root, baseline)
    notes = overlay.check_identity()
    notes.append(overlay.check_version())
    notes.append(overlay.check_app_binding())

    icons = {}
    for slot in ("composerIcon", "logo"):
        path = _resolve_asset(root, overlay.openai.get(slot))
        if path is None:
            icons[slot] = AssetReport(status="NEEDS_ASSET", slot=slot,
                                      reason=f"`{slot}` is not declared or is not a ./ path")
            continue
        if not str(path).startswith(str(root)):
            raise Refused(f"`{slot}` escapes the plugin root")
        icons[slot] = inspect_icon(path, require_square=(slot == "composerIcon"))

    # A REFUSED asset stops the build; a NEEDS_ASSET one does not. The
    # difference is who can act: NEEDS_ASSET means the operator has not supplied
    # a file yet, which is a normal state on the way to one, while REFUSED means
    # a file IS there and is not usable — shipping it would put an asset in a
    # release whose only verification is the one that just failed.
    bad = {slot: report for slot, report in icons.items()
           if report.get("status") == "REFUSED"}
    if bad:
        detail = "; ".join(f"{slot}: {r.get('reason')}" for slot, r in bad.items())
        raise Refused(f"an icon slot is present but unusable — {detail}")

    files = [p for p in root.rglob("*") if p.is_file()]
    if not any(p.name == "SKILL.md" and "skills" in p.relative_to(root).parts
               for p in files):
        raise Refused("no skills/<name>/SKILL.md: the overlay carries a plugin "
                      "with no skills, which updates nothing an operator can see")

    name = overlay.manifest["name"]
    version = overlay.version
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{name}-overlay-v{version}.zip"

    rels = sorted(p.relative_to(root).as_posix() for p in files)
    content = hashlib.sha256()
    for rel in rels:
        content.update(rel.encode())
        content.update((root / rel).read_bytes())

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in rels:
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FIXED_MODE
            zf.writestr(info, (root / rel).read_bytes(),
                        compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = {
        "schema": "serveros/fleet-app-overlay.v1",
        "distribution": DISTRIBUTION,
        "warning": ("This archive contains an app reference and a "
                    "`.codex-plugin` compatibility manifest. It is for the "
                    "PRIVATE Plugin Creator update path only; a public "
                    "submission rejects app references outright."),
        "plugin_id": overlay.plugin_id,
        "name": name,
        "version": version,
        "archive": archive.name,
        "archive_sha256": archive_sha,
        "content_sha256": content.hexdigest(),
        "members": rels,
        "icons": {k: dict(v) for k, v in icons.items()},
        "identity_notes": notes,
        "writes": [],
        "creates_plugin": False,
    }
    (out_dir / f"{name}-overlay-v{version}.release.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def check(root: Path, baseline: dict) -> dict:
    """Build twice into scratch dirs; refuse unless both digests agree."""
    import tempfile
    with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
        first = build(root, Path(one), baseline)
        second = build(root, Path(two), baseline)
    if first["archive_sha256"] != second["archive_sha256"]:
        raise Refused("two builds of one source produced different archives")
    if first["content_sha256"] != second["content_sha256"]:
        raise Refused("two builds of one source produced different content digests")
    first["stable"] = True
    return first


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True,
                        help="the operator's overlay source tree (never committed)")
    parser.add_argument("--baseline", default=str(here / "baseline.json"),
                        help="the pinned identity this overlay may not move")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.source).resolve()
    baseline = _read_json(Path(args.baseline).resolve(), "baseline")
    try:
        record = check(root, baseline) if args.check else build(root, Path(args.out).resolve(), baseline)
    except Refused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2

    print(f"distribution={record['distribution']}")
    print(f"{record['archive']} sha256={record['archive_sha256']}")
    print(f"content_sha256={record['content_sha256']}")
    for slot, report in record["icons"].items():
        print(f"  {slot}: {report['status']} ({report.get('reason') or report.get('note')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
