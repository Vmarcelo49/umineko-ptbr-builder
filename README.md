# umineko-ptbr-builder

Repo público: https://github.com/Vmarcelo49/umineko-ptbr-builder

Reproducible builder for the Brazilian Portuguese patch of *Umineko no Naku
Koro ni Saku* running on [AuroraWright/shin](https://github.com/AuroraWright/shin).

It asks for your **legally dumped NSP** and the **keys that belong to that
dump**, then extracts `data.rom`, applies the reviewed pt-BR script, builds the
ROM overlay and writes a ready-to-launch directory.

## Requirements

* Python 3.9+ (orchestrator; standard library only for the main flow).
* Git, Node.js, Bash/POSIX shell (or WSL on Windows).
* The game dump and its keys — never bundled, never uploaded.
* Prebuilt binaries in `prebuilt/<os>-<arch>/` are used when present; otherwise
  the script builds/downloads the tools (`hactool`, Rust toolchain).

## Usage

```bash
python3 builder.py --nsp "/path/to/Umineko [01006A300BA2C000].nsp" \
                   --keys-dir "/path/to/keys"
```

Options:

| Flag | Meaning |
| --- | --- |
| `--skip-extract` | reuse `work/assets/data.rom` from a previous run |
| `--out DIR` | output directory (default `dist/`) |
| `--work DIR` | working directory (default `work/`, gitignored) |
| `--full` | redo alignment from the upstream sources (slow) |
| `--verify-only` | rebuild/reverify the patched script without game data |

## Output

`dist/` receives:

* `data.rom` — symlink (or copy) of the legally extracted game data
* `mod.rom` — the pt-BR overlay (installed ahead of the original archives)
* `localized.toml` — pt-BR engine UI strings
* `shin` / `shin.exe` — prebuilt engine binary, when available
* `run.py` — cross-platform launcher
* `manifest.json` — hashes and pinned upstream revisions

Run it with:

```bash
python3 dist/run.py            # uses the default disposable save
python3 dist/run.py /path/to/SAVEDATA
```

## Design notes

* The reviewed mapping is shipped compressed in `cache/` (74 MB raw, 17 MB gz),
  so a normal build does not need to redo the 8-minute alignment/apply pass.
* `--full` regenerates everything from the pinned upstream sources and the
  `umineko-scripting` z4067 release.
* All original commercial data stays outside Git; only the toolchain and the
  reviewed mapping are versioned.
