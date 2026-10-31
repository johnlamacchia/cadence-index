#!/usr/bin/env python3
"""Validate mod entries and build index.json for the Cadence mod index.

  python scripts/cadence_index.py validate mods/my-mod.json [more entries...]   check entries (downloads each mod)
  python scripts/cadence_index.py validate --all                                check every entry in mods/
  python scripts/cadence_index.py validate --offline ...                        schema checks only, no network
  python scripts/cadence_index.py build                                         write index.json from mods/*.json
  python scripts/cadence_index.py entry my_mod.dll --id ... --name ...          write a correct mods/<id>.json for a DLL

Requires Python 3.9+ and, for the download checks, `pip install pefile`.

What a check proves, and what it does not. For every entry the downloaded file is hashed, and must match the
declared sha256 and size; it must be a 64-bit Windows DLL under 5 MiB that exports `mod_main`. That catches a
wrong or swapped file, a broken build and a file that is not a Cadence mod. It does NOT tell whether the code is
safe: a mod is native code that runs inside the game. That part is a human reading the source in the pull request.
"""
import argparse, hashlib, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODS_DIR = ROOT / "mods"
INDEX_FILE = ROOT / "index.json"

FORMAT = 1
SUPPORTED_GD = {"2.2091"}                     # GD versions Cadence has addresses for
MAX_BYTES = 5 * 1024 * 1024
REQUIRED = {"id", "name", "author", "description", "version", "gd", "file", "download", "sha256", "size", "source", "license"}

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
FILE_RE = re.compile(r"^[a-z][a-z0-9_]{0,38}\.dll$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
GD_RE = re.compile(r"^\d+\.\d+$")
GITHUB_DOWNLOAD_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/releases/download/[^/]+/[^/]+$")
GITHUB_RAW_RE = re.compile(r"^https://raw\.githubusercontent\.com/[\w.-]+/[\w.-]+/[^/]+/.+")
GITHUB_REPO_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/?$")


def check_schema(entry, path: Path):
    """List of problems with the entry itself (no network)."""
    problems = []
    if not isinstance(entry, dict):
        return ["the file must contain one JSON object"]
    keys = set(entry)
    for k in sorted(REQUIRED - keys):
        problems.append(f"missing field '{k}'")
    for k in sorted(keys - REQUIRED):
        problems.append(f"unknown field '{k}'")
    if problems:
        return problems

    def text(field, low, high):
        v = entry[field]
        if not isinstance(v, str) or not (low <= len(v.strip()) <= high) or v != v.strip():
            problems.append(f"'{field}' must be {low}-{high} characters with no leading/trailing spaces")

    text("name", 1, 40); text("author", 1, 40); text("description", 10, 300); text("version", 1, 20); text("license", 1, 40)
    if not isinstance(entry["id"], str) or not ID_RE.match(entry["id"]):
        problems.append("'id' must be 3-40 characters: lowercase letters, digits and hyphens, not starting or ending with a hyphen")
    elif path.stem != entry["id"]:
        problems.append(f"the file must be named '{entry['id']}.json' to match its id (it is '{path.name}')")
    if not isinstance(entry["file"], str) or not FILE_RE.match(entry["file"]):
        problems.append("'file' must look like my_mod.dll (lowercase letters, digits, underscores)")
    if not isinstance(entry["gd"], list) or not entry["gd"] or not all(isinstance(g, str) and GD_RE.match(g) for g in entry["gd"]):
        problems.append("'gd' must be a non-empty list of versions like [\"2.2091\"]")
    elif not (set(entry["gd"]) & SUPPORTED_GD):
        problems.append(f"'gd' must include a supported version ({', '.join(sorted(SUPPORTED_GD))})")
    if not isinstance(entry["sha256"], str) or not SHA_RE.match(entry["sha256"]):
        problems.append("'sha256' must be 64 lowercase hex characters")
    if not isinstance(entry["size"], int) or isinstance(entry["size"], bool) or not (1 <= entry["size"] <= MAX_BYTES):
        problems.append(f"'size' must be a whole number of bytes, at most {MAX_BYTES}")
    d = entry["download"]
    test_file = os.environ.get("CADENCE_INDEX_ALLOW_FILE") == "1" and isinstance(d, str) and d.startswith("file://")
    if not test_file and (not isinstance(d, str) or not (GITHUB_DOWNLOAD_RE.match(d) or GITHUB_RAW_RE.match(d))):
        problems.append("'download' must be a GitHub release asset (https://github.com/OWNER/REPO/releases/download/TAG/FILE) "
                        "or a raw.githubusercontent.com file, so it stays tied to a public repository")
    s = entry["source"]
    if not isinstance(s, str) or not GITHUB_REPO_RE.match(s):
        problems.append("'source' must be the public GitHub repository (https://github.com/OWNER/REPO)")
    return problems


def fetch(url: str) -> bytes:
    """The bytes at url, refusing anything over MAX_BYTES. file:// only when CADENCE_INDEX_ALLOW_FILE=1 (tests)."""
    if url.startswith("file://") and os.environ.get("CADENCE_INDEX_ALLOW_FILE") == "1":
        path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        data = Path(path).read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError(f"the file is larger than {MAX_BYTES} bytes")
        return data
    req = urllib.request.Request(url, headers={"User-Agent": "cadence-index-validator"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"the download is larger than {MAX_BYTES} bytes")
    return data


def check_dll(data: bytes):
    """Problems with the downloaded file as a Cadence mod."""
    try:
        import pefile
    except ImportError:
        return ["pefile is not installed (pip install pefile), so the DLL cannot be inspected"]
    if data[:2] != b"MZ":
        return ["the download is not a Windows executable (no MZ header)"]
    try:
        pe = pefile.PE(data=data, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    except pefile.PEFormatError as e:
        return [f"the download is not a valid PE file: {e}"]
    problems = []
    if pe.FILE_HEADER.Machine != 0x8664:
        problems.append("the DLL is not 64-bit (Geometry Dash is x64)")
    if not (pe.FILE_HEADER.Characteristics & 0x2000):
        problems.append("the file is not a DLL")
    exports = {e.name.decode() for e in getattr(pe, "DIRECTORY_ENTRY_EXPORT", None).symbols if e.name} \
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else set()
    if "mod_main" not in exports:
        problems.append("the DLL does not export mod_main, so Cadence cannot load it")
    return problems


def check_online(entry):
    problems = []
    try:
        data = fetch(entry["download"])
    except Exception as e:                                         # network, 404, too large...
        return [f"could not download {entry['download']}: {e}"]
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        problems.append(f"sha256 mismatch: the file at that URL is {digest}, the entry says {entry['sha256']}")
    if len(data) != entry["size"]:
        problems.append(f"size mismatch: the file is {len(data)} bytes, the entry says {entry['size']}")
    problems += check_dll(data)
    try:
        req = urllib.request.Request(entry["source"], method="HEAD", headers={"User-Agent": "cadence-index-validator"})
        if os.environ.get("CADENCE_INDEX_ALLOW_FILE") != "1":
            urllib.request.urlopen(req, timeout=30).close()
    except Exception as e:
        problems.append(f"the source repository {entry['source']} is not reachable (is it public?): {e}")
    return problems


def load_entry(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, ValueError) as e:
        return None, [f"cannot read as JSON: {e}"]


def validate(paths, offline):
    failed = False
    seen_ids, seen_files = {}, {}
    for path in paths:
        entry, problems = load_entry(path)
        if entry is not None:
            problems = check_schema(entry, path)
            if not problems:
                for table, key, label in ((seen_ids, entry["id"], "id"), (seen_files, entry["file"], "file")):
                    if key in table:
                        problems.append(f"'{label}' {key!r} is already used by {table[key]}")
                    table[key] = path.name
            if not problems and not offline:
                problems = check_online(entry)
        if problems:
            failed = True
            print(f"FAIL  {path.name}")
            for p in problems:
                print(f"        - {p}")
        else:
            print(f"ok    {path.name}")
    return not failed


def build():
    entries = []
    for path in sorted(MODS_DIR.glob("*.json")):
        entry, problems = load_entry(path)
        if problems or check_schema(entry, path):
            sys.exit(f"{path.name} is invalid; run `validate` first")
        entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Keep the old timestamp when nothing changed, so a rebuild does not make a pointless commit.
    if INDEX_FILE.exists():
        try:
            old = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if old.get("format") == FORMAT and old.get("mods") == entries:
                updated = old.get("updated", updated)
        except ValueError:
            pass
    INDEX_FILE.write_text(json.dumps({"format": FORMAT, "updated": updated, "mods": entries}, indent=2) + "\n", encoding="utf-8")
    print(f"index.json: {len(entries)} mod(s)")

def make_entry(args):
    dll = Path(args.dll)
    data = dll.read_bytes()
    entry = {
        "id": args.id, "name": args.name, "author": args.author, "description": args.description,
        "version": args.version, "gd": args.gd or ["2.2091"], "file": args.file or dll.name.lower(),
        "download": args.download, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        "source": args.source, "license": args.license,
    }
    MODS_DIR.mkdir(exist_ok=True)
    path = MODS_DIR / f"{args.id}.json"
    problems = check_schema(entry, path) + check_dll(data)
    if problems:
        print("not written:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"wrote {shown}\nsha256 {entry['sha256']}  size {entry['size']}\n"
          f"Upload {dll.name} to the release at the download URL, then run:\n  python scripts/cadence_index.py validate {shown}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="*")
    v.add_argument("--all", action="store_true")
    v.add_argument("--offline", action="store_true")
    sub.add_parser("build")
    e = sub.add_parser("entry", help="write mods/<id>.json for a mod DLL (hash and size filled in for you)")
    e.add_argument("dll"); e.add_argument("--id", required=True); e.add_argument("--name", required=True)
    e.add_argument("--author", required=True); e.add_argument("--description", required=True)
    e.add_argument("--version", required=True); e.add_argument("--download", required=True, help="URL the DLL will be downloaded from")
    e.add_argument("--source", required=True, help="the public GitHub repository"); e.add_argument("--license", required=True)
    e.add_argument("--gd", nargs="*", help="GD versions (default 2.2091)"); e.add_argument("--file", help="file name in mods/ (default: the DLL's name)")
    args = ap.parse_args()
    if args.cmd == "entry":
        make_entry(args)
        return
    if args.cmd == "build":
        build()
        return
    paths = sorted(MODS_DIR.glob("*.json")) if args.all else [Path(f) for f in args.files]
    if not paths:
        print("nothing to validate")
        return
    sys.exit(0 if validate(paths, args.offline) else 1)


if __name__ == "__main__":
    main()
