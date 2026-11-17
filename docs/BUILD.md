# Build pipeline details

1. `builder.py` validates the NSP and the key directory.
2. Clones/pins Shin `fork`, Catbox `rebirth` and Florescer `main`.
3. Builds/downloads tools as needed. Prebuilt Linux x86_64 binaries for Shin
   and hactool are in `prebuilt/linux-x86_64/`.
4. Derives the title key from the NSP ticket with `nsz`, then extracts the
   romfs with `hactool` (PFS0 → NCA → `data.rom`).
5. Decompresses the reviewed mapping from `cache/` and applies 69,702
   voice-anchored messages to a copy of `script.rb`.
6. Runs the logical verifier (69,702/69,702 exact text, voices and tags).
7. Copies the Latin FNT files from the Spanish Catbox fork, recreates the `@u`
   mappings from the approved segments, and runs Catbox's `build.sh`.
8. Writes `dist/mod.rom`, `dist/localized.toml`, the engine binary and a
   cross-platform `run.py` launcher.

Verified output hash (Linux x86_64, pinned revisions):

```
mod.rom  d46fa6865002a8cbdf4891f9a9118f71a43f0876b56fabc80aa6db1c1cfe27d5
```

Use `--verify-only` to skip game-data extraction and only rebuild/reverify the
patched script; use `--skip-extract` to reuse an already extracted `data.rom`.
