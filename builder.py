#!/usr/bin/env python3
"""Reproducible builder for the Brazilian Portuguese Umineko Saku patch.

The script asks for a legally dumped Nintendo Switch NSP and for the keys that
belong to that dump, then performs every step needed to produce a translated
game directory for AuroraWright's Shin engine.

Design goals:
  * Python 3 standard library for orchestration (works on Linux/macOS/Windows).
  * External tools are downloaded or compiled only when missing.
  * Prebuilt Linux x86_64 binaries live in ``prebuilt/`` and are used when
    available, so a full Rust/Ruby rebuild is not required.
  * Inputs (NSP, keys, extracted romfs, saves) are never copied into Git.

Usage:
    python3 builder.py --nsp "/path/game.nsp" --keys-dir "/path/keys"
    python3 builder.py --nsp ... --keys-dir ... --full   # redo alignment
    python3 builder.py --verify-only                     # check cached artifacts
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

REPOS = {
    "shin": ("https://github.com/AuroraWright/shin.git", "fork", "8875fd49f1961265d4101c541027a11db629aa9b"),
    "catbox": ("https://github.com/andOlga/umineko-catbox-english.git", "rebirth", "3d697ac8ef8d525ecb25f43e01f1499f91ccdcc9"),
    "florescer": ("https://github.com/Monaco-a-Knox/Florescer.git", "main", "ae520db7158dbb94e4ed8ae1c4e6a21eb5949751"),
}
RELEASE = "https://github.com/umineko-project/umineko-scripting/releases/download/z4067"
BUILDER_RELEASE = "https://github.com/Vmarcelo49/umineko-ptbr-builder/releases/download/prebuilt"
SHIN_ASSET = "shin-linux-x86_64"
PT_ZIP = "umineko-pt-scripts_r4067_dev.zip"
FONTS = {
    "newrodin-medium.fnt": "https://raw.githubusercontent.com/Lovahi/umineko-catbox-spanish/master/romfs/newrodin-medium.fnt",
    "newrodin-bold.fnt": "https://raw.githubusercontent.com/Lovahi/umineko-catbox-spanish/master/romfs/newrodin-bold.fnt",
    "system.fnt": "https://raw.githubusercontent.com/Lovahi/umineko-catbox-spanish/master/romfs/system.fnt",
}
HACTOOL_REPO = "https://github.com/SciresM/hactool.git"


class Step:
    def __init__(self, name: str, log: Path) -> None:
        self.name = name
        self.log = log

    def say(self, text: str) -> None:
        line = f"[{self.name}] {text}"
        print(line, flush=True)
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def run(cmd: list[str], step: Step, *, cwd: Path | None = None, env: dict | None = None) -> None:
    step.say("$ " + " ".join(str(x) for x in cmd))
    result = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with step.log.open("a", encoding="utf-8") as fh:
        fh.write(result.stdout)
    if result.returncode != 0:
        raise SystemExit(f"command failed (exit {result.returncode}): {cmd}\n{result.stdout[-2000:]}")


def download(url: str, dest: Path, step: Step) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        step.say(f"cached: {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    step.say(f"downloading {url} -> {dest.name}")
    req = urllib.request.Request(url, headers={"User-Agent": "umineko-ptbr-builder"})
    with urllib.request.urlopen(req, timeout=120) as response, dest.open("wb") as fh:
        shutil.copyfileobj(response, fh)


def clone_repos(work: Path, step: Step) -> dict[str, Path]:
    src = work / "upstream"
    src.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, (url, branch, commit) in REPOS.items():
        path = src / name
        if not (path / ".git").exists():
            run(["git", "clone", "--branch", branch, url, str(path)], step)
        head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if head != commit:
            step.say(f"warning: {name} HEAD {head} != pinned {commit}")
        paths[name] = path
    return paths


def ensure_tools(work: Path, step: Step) -> dict[str, str]:
    tools: dict[str, str] = {}
    prebuilt = ROOT / "prebuilt" / f"{platform.system().lower()}-{platform.machine().lower()}"
    for name in ("shin", "hactool"):
        candidate = prebuilt / name
        if candidate.exists():
            tools[name] = str(candidate)
        else:
            found = shutil.which(name)
            if found:
                tools[name] = found
    if "shin" not in tools:
        step.say("fetching prebuilt Shin binary from GitHub Release")
        try:
            download(f"{BUILDER_RELEASE}/{SHIN_ASSET}", prebuilt / "shin", step)
            (prebuilt / "shin").chmod(0o755)
            tools["shin"] = str(prebuilt / "shin")
        except Exception as exc:
            step.say(f"release download unavailable ({exc}); build Shin with cargo")
    if "hactool" not in tools:
        step.say("hactool not found; building it from source")
        src = work / "hactool-src"
        if not (src / ".git").exists():
            run(["git", "clone", "--depth", "1", HACTOOL_REPO, str(src)], step)
        shutil.copy(src / "config.mk.template", src / "config.mk")
        run(["make", "-j", str(os.cpu_count() or 2)], step, cwd=src)
        tools["hactool"] = str(src / "hactool")
    tools["node"] = shutil.which("node") or ""
    tools["git"] = shutil.which("git") or ""
    if not tools["node"]:
        step.say("warning: node is required by Catbox's replace_chars.js")
    return tools


def python_venv(work: Path, step: Step) -> Path:
    venv = work / "venv"
    if not (venv / "bin" / "python").exists() and not (venv / "Scripts" / "python.exe").exists():
        run([sys.executable, "-m", "venv", str(venv)], step)
    py = venv / "bin" / "python" if os.name != "nt" else venv / "Scripts" / "python.exe"
    run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "nsz", "websockets", "Pillow"], step)
    return py


def write_filtered_keys(keys_dir: Path, work: Path, step: Step) -> tuple[Path, Path]:
    prod = keys_dir / "prod.keys"
    title = keys_dir / "title.keys"
    if not prod.is_file():
        raise SystemExit(f"missing prod.keys in {keys_dir}")
    out = work / "keys" / "hactool-prod.keys"
    out.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with out.open("w", encoding="utf-8") as dst:
        for line in prod.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                value = line.split("=", 1)[1].strip()
                if not re.fullmatch(r"[0-9a-fA-F]+", value) or len(value) not in (32, 64):
                    continue
            dst.write(line + "\n")
            kept += 1
    step.say(f"filtered prod.keys: {kept} usable lines")
    return out, title


def extract_rom(nsp: Path, keys_dir: Path, work: Path, tools: dict[str, str], venv_py: Path, step: Step) -> Path:
    data_rom = work / "assets" / "data.rom"
    if data_rom.exists() and data_rom.stat().st_size > 1_000_000_000:
        step.say(f"using existing data.rom ({data_rom.stat().st_size} bytes)")
        return data_rom
    step.say("deriving title key from NSP ticket")
    keyhome = work / "keyhome" / ".switch"
    keyhome.mkdir(parents=True, exist_ok=True)
    prod, title = write_filtered_keys(keys_dir, work, step)
    shutil.copy(prod, keyhome / "prod.keys")
    env = os.environ.copy()
    env["HOME"] = str(work / "keyhome")
    env["XDG_CONFIG_HOME"] = str(work / "keyhome" / ".config")
    keyfile = work / "titlekeys.txt"
    run([str(venv_py), "-m", "nsz", "--titlekeys", "--keys", str(keys_dir), str(nsp)], step, cwd=work)
    if not keyfile.exists():
        candidates = list(work.glob("**/titlekeys.txt"))
        if not candidates:
            raise SystemExit("nsz did not produce titlekeys.txt")
        keyfile = candidates[0]
    line = keyfile.read_text(encoding="utf-8").splitlines()[0]
    rid, key = line.split("|", 2)[:2]
    (keyhome / "title.keys").write_text(f"{rid} = {key}\n", encoding="utf-8")

    hactool = tools["hactool"]
    pfs0 = work / "nsp_extract"
    pfs0.mkdir(parents=True, exist_ok=True)
    run([hactool, "-k", str(prod), "-t", "pfs0", "--pfs0dir", str(pfs0), "--disablekeywarns", str(nsp)], step)
    ncas = sorted(pfs0.glob("*.nca"), key=lambda p: p.stat().st_size, reverse=True)
    if not ncas:
        raise SystemExit("no NCA found in NSP")
    data_rom.parent.mkdir(parents=True, exist_ok=True)
    env2 = os.environ.copy()
    env2["HOME"] = str(work / "keyhome")
    run([hactool, "-t", "nca", "--romfsdir", str(data_rom.parent), str(ncas[0])], step, env=env2)
    if not data_rom.exists():
        raise SystemExit("hactool did not produce data.rom")
    step.say(f"extracted data.rom ({data_rom.stat().st_size} bytes)")
    return data_rom


def load_cache(name: str, work: Path, step: Step) -> list[dict[str, Any]]:
    gz = ROOT / "cache" / f"{name}.jsonl.gz"
    raw = work / "cache" / f"{name}.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.exists():
        with gzip.open(gz, "rb") as src, raw.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    step.say(f"loaded cache {name}: {raw.stat().st_size} bytes")
    return [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()]


def apply_patch(repos: dict[str, Path], work: Path, step: Step) -> Path:
    from umineko_ptbr.up_source import apply_up_edits

    script = repos["catbox"] / "script.rb"
    targets = load_cache("catbox_messages", work, step)
    review = load_cache("combined.review", work, step)
    target_file = work / "cache" / "catbox_messages.jsonl"
    review_file = work / "cache" / "combined.review.jsonl"
    output = work / "script.ptbr.rb"
    log = apply_up_edits(script, review_file, target_file, output, work / "apply.log.json")
    step.say(f"applied {len(log['applied'])} messages; sha256={log['output_sha256']}")
    return output


def prepare_catbox(repos: dict[str, Path], work: Path, patched: Path, step: Step) -> Path:
    build = work / "catbox-build"
    if build.exists():
        shutil.rmtree(build)
    shutil.copytree(repos["catbox"], build, ignore=shutil.ignore_patterns(".git"))
    shutil.copy(patched, build / "script.rb")
    # build.sh copies these over the Ruby compiler's font metrics when present.
    manifests = build / "font_manifests"
    if not manifests.exists():
        shutil.copytree(build / "locale_base" / "font_manifests", manifests)
    for name in FONTS:
        src = ROOT / "assets" / name
        if not src.exists():
            download(FONTS[name], src, step)
        shutil.copy(src, build / "romfs" / name)

    # Recreate the @u mapping from the approved Portuguese segments only —
    # mapping unrelated English text changes the compiled ROM needlessly.
    review = load_cache("combined.review", work, Step("chars", work / "build.log"))
    chars: set[str] = set()
    for row in review:
        if not row.get("approved"):
            continue
        for seg in row.get("segments", []):
            chars.update(ch for ch in seg["text"] if ord(ch) > 127)
    (build / "replace_chars.txt").write_text("\n".join(sorted(chars)) + "\n", encoding="utf-8")
    js = build / "locale_base" / "replace_chars.js"
    if js.exists():
        shutil.copy(js, build / "replace_chars.js")
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is required to apply @u mappings")
    run([node, "replace_chars.js"], step, cwd=build)
    return build


def build_rom(build: Path, work: Path, step: Step) -> Path:
    ruby = shutil.which("ruby") or str(Path.home() / ".local/ruby/bin/ruby")
    env = os.environ.copy()
    env["PATH"] = str(Path(ruby).parent) + os.pathsep + env.get("PATH", "")
    for stale in ("mods", "UminekoCatboxEnglish", "patch.rom", "patch.snr", "patch_atmos.zip", "patch_yuzu.zip"):
        path = build / stale
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    run(["sh", "build.sh"], step, cwd=build, env=env)
    rom = build / "mods/contents/01006a300ba2c000/romfs/patch.rom"
    if not rom.exists():
        raise SystemExit("build.sh did not produce patch.rom")
    return rom


def verify(patched: Path, repos: dict[str, Path], work: Path, step: Step) -> None:
    from umineko_ptbr.validate import validate_patch
    from umineko_ptbr.verify import verify_review_only

    review = work / "cache" / "combined.review.jsonl"
    result = verify_review_only(patched, repos["catbox"] / "script.rb", review)
    step.say(f"logical verification: {result['checked']} rows, ok={result['ok']}")
    if not result["ok"]:
        raise SystemExit(f"logical verification failed: {result['errors'][:5]}")


def write_launcher(out: Path, step: Step) -> None:
    launcher = out / "run.py"
    launcher.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        """Launch the translated game from this directory."""
        import os, pathlib, platform, subprocess, sys
        here = pathlib.Path(__file__).resolve().parent
        binary = here / "shin"
        if platform.system() == "Windows":
            binary = here / "shin.exe"
        cmd = [str(binary), "--game", "umineko", "--assets-dir", str(here)]
        if len(sys.argv) > 1:
            cmd += ["--save-file", sys.argv[1]]
        print("Launching:", " ".join(cmd))
        raise SystemExit(subprocess.call(cmd))
    '''), encoding="utf-8")
    step.say(f"wrote launcher: {launcher}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nsp", help="path to the legally dumped NSP")
    parser.add_argument("--keys-dir", help="directory containing prod.keys and title.keys")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    parser.add_argument("--work", default=str(ROOT / "work"), help="working directory (gitignored)")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-extract", action="store_true", help="reuse an existing data.rom")
    parser.add_argument("--full", action="store_true", help="redo extraction/alignment from sources")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    out = Path(args.out).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    log = work / "build.log"
    step = Step("builder", log)

    step.say(f"platform={platform.platform()} python={sys.version.split()[0]}")
    if args.verify_only:
        repos = clone_repos(work, step)
        patched = work / "script.ptbr.rb"
        if not patched.exists():
            patched = apply_patch(repos, work, step)
        verify(patched, repos, work, step)
        return 0

    if not args.nsp or not args.keys_dir:
        raise SystemExit("both --nsp and --keys-dir are required (or use --verify-only)")
    nsp = Path(args.nsp).expanduser().resolve()
    keys = Path(args.keys_dir).expanduser().resolve()
    if not nsp.is_file():
        raise SystemExit(f"NSP not found: {nsp}")
    if not keys.is_dir():
        raise SystemExit(f"keys directory not found: {keys}")
    step.say(f"NSP={nsp} ({nsp.stat().st_size} bytes) keys={keys}")

    repos = clone_repos(work, step)
    tools = ensure_tools(work, step)
    venv_py = python_venv(work, step)
    data_rom = work / "assets" / "data.rom"
    if args.skip_extract and data_rom.exists():
        step.say("skipping NSP extraction")
    else:
        extract_rom(nsp, keys, work, tools, venv_py, step)

    patched = apply_patch(repos, work, step)
    verify(patched, repos, work, step)
    build = prepare_catbox(repos, work, patched, step)
    rom = build_rom(build, work, step)

    shutil.copy(rom, out / "mod.rom")
    shutil.copy(ROOT / "assets/localized.toml", out / "localized.toml")
    if not (out / "data.rom").exists():
        target = out / "data.rom"
        try:
            target.symlink_to(data_rom)
        except (OSError, NotImplementedError):
            shutil.copy(data_rom, target)
    prebuilt_shin = ROOT / "prebuilt" / f"{platform.system().lower()}-{platform.machine().lower()}" / "shin"
    if prebuilt_shin.exists():
        shutil.copy(prebuilt_shin, out / ("shin.exe" if os.name == "nt" else "shin"))
    elif "shin" in tools:
        shutil.copy(tools["shin"], out / ("shin.exe" if os.name == "nt" else "shin"))
    else:
        step.say("warning: no Shin binary available; build it with cargo build --release -p shin")
    write_launcher(out, step)

    manifest = {
        "rom_sha256": __import__("hashlib").sha256((out / "mod.rom").read_bytes()).hexdigest(),
        "patched_script_sha256": __import__("hashlib").sha256(patched.read_bytes()).hexdigest(),
        "revisions": {k: subprocess.check_output(["git", "-C", str(v), "rev-parse", "HEAD"], text=True).strip() for k, v in repos.items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    step.say(f"DONE: {out} (mod.rom sha256={manifest['rom_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
