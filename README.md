# Cadence mod index

The list of mods for [Cadence](https://github.com/johnlamacchia/cadence), the mod loader for Geometry Dash 2.2091.

- **`mods/<id>.json`**: one small file per mod. This is what you edit.
- **`index.json`**: built from `mods/` by a bot after every merge. This is what the game downloads:
  `https://raw.githubusercontent.com/johnlamacchia/cadence-index/main/index.json`. Don't edit it by hand.

The mod files themselves are not stored here. Each entry points at a download on the author's own GitHub release, so
the author stays in control of their mod and the index only ever holds a name, a link and a hash.

## Adding your mod

You need a public GitHub repository with your mod's source, and your built `.dll` attached to a GitHub release.

1. **Release your mod.** Create a release on your repository and attach the DLL (for example
   `https://github.com/you/my-mod/releases/download/v1.0.0/my_mod.dll`). The file name must be lowercase letters, digits
   and underscores, ending in `.dll`.
2. **Make the entry.** In a clone of this repo (needs `pip install pefile`):

   ```
   python scripts/cadence_index.py entry path/to/my_mod.dll ^
       --id my-mod --name "My Mod" --author you ^
       --description "One sentence about what it does." --version 1.0.0 ^
       --download https://github.com/you/my-mod/releases/download/v1.0.0/my_mod.dll ^
       --source https://github.com/you/my-mod --license MIT
   ```

   That writes `mods/my-mod.json` with the SHA-256 and size filled in. (`^` is the Windows line continuation; use `\` on Linux.)
3. **Check it.** `python scripts/cadence_index.py validate mods/my-mod.json` downloads your DLL from the URL and checks it,
   the same way the pull request check will.
4. **Open a pull request** adding that one file. A bot re-runs the check; a maintainer reads your source before merging.

To release a new version, upload the new DLL and change `version`, `download`, `sha256` and `size` in your entry
(running the `entry` command again regenerates them).

## The entry format

```json
{
  "id": "my-mod",
  "name": "My Mod",
  "author": "you",
  "description": "One sentence about what it does.",
  "version": "1.0.0",
  "gd": ["2.2091"],
  "file": "my_mod.dll",
  "download": "https://github.com/you/my-mod/releases/download/v1.0.0/my_mod.dll",
  "sha256": "64 lowercase hex characters",
  "size": 20992,
  "source": "https://github.com/you/my-mod",
  "license": "MIT"
}
```

| Field | Rule |
|---|---|
| `id` | 3-40 characters: lowercase letters, digits, hyphens. The file must be `mods/<id>.json`. |
| `name`, `author` | 1-40 characters. |
| `description` | 10-300 characters. |
| `version` | Free text, up to 20 characters (semantic versions are best). |
| `gd` | GD versions the DLL was built for. Must include a supported one (currently `2.2091`). |
| `file` | The file name the mod gets in the game's `mods/` folder. Unique across the index. |
| `download` | A GitHub release asset (`https://github.com/OWNER/REPO/releases/download/TAG/FILE`) or a `raw.githubusercontent.com` file. |
| `sha256`, `size` | Of the exact file at `download`. |
| `source` | The public repository (`https://github.com/OWNER/REPO`). |
| `license` | Your license, e.g. `MIT`. |

## What the check does, and what it doesn't

For every entry the bot downloads the file and checks that the hash and size match, that it is a 64-bit Windows DLL under
5 MiB, and that it exports `mod_main`. That catches a wrong or swapped file, a broken build, and something that isn't a
Cadence mod.

**It does not tell whether the code is safe.** A mod is native code that runs inside Geometry Dash, with all the
access that implies. Only install mods whose source you have looked at or whose author you trust, and note that a merged
entry means a maintainer read the source at the time, not that every later version of the author's repository is safe.
Because entries are pinned to a hash, an author cannot change a listed file afterwards without a new pull request.

To report a malicious or broken mod, open an issue; removing its entry takes it out of the index.

## For maintainers

- `python scripts/selftest.py` tests the validator and the index builder with no network.
- `.github/workflows/validate.yml` checks every pull request; `build-index.yml` rebuilds `index.json` after a merge.
- `SUPPORTED_GD` in `scripts/cadence_index.py` lists the GD versions the index accepts.

MIT licensed (see `LICENSE`).
