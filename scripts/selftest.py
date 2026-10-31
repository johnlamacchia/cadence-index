#!/usr/bin/env python3
"""Self-test of the validator and the index builder, with no network.

  pip install pefile
  python scripts/selftest.py

tests/fixtures/example_mod.dll is a real Cadence mod (one of the examples). Each case below builds an entry that
is wrong in exactly one way and checks that validation fails for that reason, then checks that the good entry
passes. A validator that accepts everything would pass a test that only tried good input.
"""
import hashlib, json, os, sys, tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

os.environ["CADENCE_INDEX_ALLOW_FILE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cadence_index as ci

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "example_mod.dll"
failed = 0


def check(ok, what):
    global failed
    print(("  ok    " if ok else "  FAIL  ") + what)
    failed += 0 if ok else 1


def entry_for(tmp: Path, name: str, data: bytes, **overrides):
    blob = tmp / f"{name}.bin"
    blob.write_bytes(data)
    entry = {
        "id": name, "name": "Test Mod", "author": "tester", "description": "A mod used by the self-test.",
        "version": "1.0.0", "gd": ["2.2091"], "file": "test_mod.dll", "download": blob.as_uri(),
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        "source": "https://github.com/example/test-mod", "license": "MIT",
    }
    entry.update(overrides)
    for k in [k for k, v in overrides.items() if v is None]:
        del entry[k]
    path = tmp / f"{entry.get('id', name)}.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def run(paths, offline=False):
    out = StringIO()
    with redirect_stdout(out):
        ok = ci.validate(paths, offline)
    return ok, out.getvalue()


def rejects(path, expected, why, offline=False):
    ok, text = run([path], offline)
    check((not ok) and expected in text, f"rejects {why}")
    if ok or expected not in text:
        print("        got:", text.strip().replace("\n", " | "))


good = FIXTURE.read_bytes()
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)

    print("a good entry")
    ok, text = run([entry_for(tmp, "good-mod", good)])
    check(ok, "a correct entry passes, including the download, hash, size and mod_main checks")

    print("the downloaded file is wrong")
    rejects(entry_for(tmp, "bad-hash", good, sha256="0" * 64), "sha256 mismatch", "a hash that does not match the file")
    rejects(entry_for(tmp, "bad-size", good, size=len(good) + 1), "size mismatch", "a size that does not match the file")
    rejects(entry_for(tmp, "not-a-dll", b"this is just text, not a program"), "not a Windows executable", "a file that is not a program")
    rejects(entry_for(tmp, "no-main", good.replace(b"mod_main", b"mod_mine")), "does not export mod_main", "a DLL that is not a Cadence mod")
    rejects(entry_for(tmp, "too-big", good + b"\0" * (ci.MAX_BYTES + 10), size=len(good)), "larger than", "a download that is over 5 MiB although the entry declares a small size")
    rejects(entry_for(tmp, "huge-declared", good, size=ci.MAX_BYTES + 1), "at most", "an entry that declares a size over 5 MiB", offline=True)
    rejects(entry_for(tmp, "missing-file", good, download=(tmp / "does_not_exist.bin").as_uri()), "could not download", "a download that fails")

    print("the entry itself is wrong")
    rejects(entry_for(tmp, "no-license", good, license=None), "missing field 'license'", "a missing field", offline=True)
    rejects(entry_for(tmp, "extra-field", good, homepage="https://example.com"), "unknown field 'homepage'", "an unknown field", offline=True)
    rejects(entry_for(tmp, "UPPER", good, id="Bad_Id"), "'id' must be", "a malformed id", offline=True)
    p = entry_for(tmp, "name-mismatch", good)
    d = json.loads(p.read_text()); d["id"] = "other-name"; p.write_text(json.dumps(d))
    rejects(p, "must be named 'other-name.json'", "an id that does not match the file name", offline=True)
    rejects(entry_for(tmp, "bad-file", good, file="../evil.exe"), "'file' must look like", "a path or non-dll file name", offline=True)
    rejects(entry_for(tmp, "short-desc", good, description="too short"), "'description' must be", "a too-short description", offline=True)
    rejects(entry_for(tmp, "old-gd", good, gd=["2.113"]), "must include a supported version", "a GD version Cadence does not support", offline=True)
    rejects(entry_for(tmp, "http-url", good, download="http://github.com/a/b/releases/download/v1/x.dll"), "'download' must be a GitHub release", "a plain-http download", offline=True)
    rejects(entry_for(tmp, "other-host", good, download="https://example.com/x.dll"), "'download' must be a GitHub release", "a download from an arbitrary host", offline=True)
    rejects(entry_for(tmp, "bad-source", good, source="https://example.com/repo"), "'source' must be the public GitHub", "a source that is not a GitHub repo", offline=True)
    rejects(entry_for(tmp, "bad-sha", good, sha256="XYZ"), "'sha256' must be 64", "a malformed sha256", offline=True)

    print("two entries that clash")
    a = entry_for(tmp, "dup-a", good)
    b = entry_for(tmp, "dup-b", good)           # same file name 'test_mod.dll'
    ok, text = run([a, b])
    check((not ok) and "already used by dup-a.json" in text and "ok    dup-a.json" in text, "the second entry using the same mod file name is rejected, the first stays valid")

    print("the entry helper")
    ci.MODS_DIR = tmp / "made"
    class A: pass
    a = A(); a.dll = str(FIXTURE); a.id = "made-mod"; a.name = "Made Mod"; a.author = "tester"; a.description = "Made by the entry helper."
    a.version = "1.0.0"; a.download = "https://github.com/example/made/releases/download/v1/made_mod.dll"; a.source = "https://github.com/example/made"
    a.license = "MIT"; a.gd = None; a.file = None
    with redirect_stdout(StringIO()):
        ci.make_entry(a)
    made = json.loads((tmp / "made" / "made-mod.json").read_text())
    check(made["sha256"] == hashlib.sha256(good).hexdigest() and made["size"] == len(good) and made["file"] == "example_mod.dll", "fills in the hash, size and file name from the DLL")
    ok, text = run([tmp / "made" / "made-mod.json"], offline=True)
    check(ok, "and the entry it writes passes the schema checks")

    print("building index.json")
    mods = tmp / "mods"; mods.mkdir()
    ci.MODS_DIR, ci.INDEX_FILE = mods, tmp / "index.json"
    for name in ("zeta-mod", "alpha-mod"):
        e = json.loads(entry_for(tmp, name, good, file=name.replace("-", "_") + ".dll").read_text())
        (mods / f"{name}.json").write_text(json.dumps(e))
    with redirect_stdout(StringIO()):
        ci.build()
    first = json.loads(ci.INDEX_FILE.read_text())
    check(first["format"] == 1 and [m["id"] for m in first["mods"]] == ["alpha-mod", "zeta-mod"], "the index lists the mods sorted by id")
    with redirect_stdout(StringIO()):
        ci.build()
    check(json.loads(ci.INDEX_FILE.read_text())["updated"] == first["updated"], "rebuilding with no change keeps the timestamp (no pointless commit)")

print()
if failed:
    print(f"{failed} check(s) failed")
    sys.exit(1)
print("index self-test: all passed")
