#!/usr/bin/env python3
"""One-command uninstall for the Cache Meter plugin.

Removes BOTH halves Hermes installs:

  1. the desktop chip  <hermes home>/desktop-plugins/cache-meter
  2. the agent package <hermes home>/plugins/cache-meter  (via `hermes plugins remove`)

and cleans up the debris a failed removal can leave behind:

  - hidden staging dirs  plugins/.cache-meter.remove-*
  - phantom cache-meter entries those staging dirs put into config.yaml
    (they make Settings show ghost "cache-meter" rows that cannot toggle)

Run it INSTEAD of `hermes plugins remove` - it performs that step itself.
Safe to run twice, tolerant of partial uninstalls: whatever is already gone
is skipped. If the agent folder is locked by the running gateway
("Access is denied"), quit Hermes completely and run this script again.

Stdlib only; works on Windows, macOS and Linux (any Python >= 3.9).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ID = "cache-meter"


def find_hermes_home() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        candidates.append(Path(env))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            candidates.append(Path(local) / "hermes")
    candidates.append(Path.home() / ".hermes")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def retry_rmtree(path: Path, attempts: int = 5, delay: float = 1.0) -> bool:
    """rmtree that retries: Hermes' folder watchers hold transient handles."""
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except (PermissionError, OSError):
            if i == attempts - 1:
                return False
            time.sleep(delay)
    return False


def remove_agent_via_cli() -> tuple[bool, str]:
    """Preferred path: Hermes' own remover (it also fixes config.yaml)."""
    try:
        proc = subprocess.run(
            ["hermes", "plugins", "remove", PLUGIN_ID],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, OSError) as exc:
        return False, f"hermes CLI unavailable ({exc})"
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    detail = " | ".join(tail[-2:]) if tail else ""
    return proc.returncode == 0, detail


def manual_remove_agent(plugins_dir: Path) -> tuple[bool, str]:
    """Fallback: delete the package in place, with retries.

    Rename-aside (what the Hermes CLI does) fails on Windows while the desktop
    app's folder watcher holds a directory handle anywhere inside the package
    (observed on plugins/cache-meter/desktop). Deletion succeeds under the
    same conditions, so rename is skipped entirely. If some files still prove
    undeletable, the leftovers are reported.
    """
    pkg = plugins_dir / PLUGIN_ID
    if not pkg.is_dir():
        return True, "already gone"

    if retry_rmtree(pkg, attempts=8, delay=1.5):
        if not pkg.exists():
            return True, "removed"
    leftovers = sorted(p.name for p in pkg.rglob("*"))[:5]
    return False, (
        f"agent folder could not be fully deleted (leftovers: {', '.join(leftovers)}...). "
        "Quit Hermes completely and run this script again."
    )


_ITEM_RE = re.compile(r"^(\s*-\s*)([^\s#].*?)\s*(?:#.*)?\r?\n?$")


def clean_config(config_path: Path) -> tuple[bool, str]:
    """Drop cache-meter list items (real + phantom staging entries) from
    plugins.enabled/disabled and the entries.cache-meter block.

    Surgical text edit: only lines whose value mentions cache-meter inside
    those two lists are removed; everything else passes through untouched.
    A .bak backup is written before any change.
    """
    try:
        original = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, "no config.yaml found"

    lines = original.splitlines(keepends=True)
    out: list[str] = []
    changed = False
    removed: list[str] = []

    in_plugins = False
    section: str | None = None
    section_indent = 0
    header_index: dict[str, int] = {}
    items_seen: dict[str, int] = {k: 0 for k in ("enabled", "disabled", "entries")}
    items_removed: dict[str, int] = {k: 0 for k in ("enabled", "disabled", "entries")}
    skipping_block = False
    skip_indent = 0

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if skipping_block:
            if stripped == "" or indent > skip_indent:
                changed = True
                continue
            skipping_block = False

        if stripped == "":
            # Blank lines are neutral: pass through without leaving the
            # plugins block (hand-edited configs have them between items).
            out.append(line)
            continue

        if indent == 0:
            in_plugins = stripped == "plugins:"
            section = None
            out.append(line)
            continue

        if in_plugins and stripped in ("enabled:", "disabled:", "entries:"):
            section = stripped[:-1]
            section_indent = indent
            header_index[section] = len(out)
            if section in items_seen:
                items_seen[section] = 0
                items_removed[section] = 0
            out.append(line)
            continue

        if in_plugins and section in ("enabled", "disabled") and indent > section_indent:
            if _ITEM_RE.match(line):
                items_seen[section] += 1

            m = _ITEM_RE.match(line)
            if m and PLUGIN_ID in m.group(2):
                removed.append(m.group(2))
                items_removed[section] += 1
                changed = True
                continue

        if in_plugins and section == "entries" and indent > section_indent and not skipping_block:
            key = stripped.split(":", 1)[0].strip().strip("\"'")
            if key and ":" in stripped:
                items_seen["entries"] += 1
            if key == PLUGIN_ID:
                removed.append(f"entries.{key}")
                items_removed["entries"] += 1
                changed = True
                skipping_block = True
                skip_indent = indent
                continue

        out.append(line)

    # A block whose every item was removed must become an explicit empty
    # collection (`key: []` / `key: {}`), not a bare `key:` header.
    for section, empty in (("enabled", "[]"), ("disabled", "[]"), ("entries", "{}")):
        if items_seen[section] > 0 and items_removed[section] == items_seen[section]:
            idx = header_index.get(section)
            if idx is not None:
                header_line = out[idx]
                eol = "\r\n" if header_line.endswith("\r\n") else "\n"
                indent_str = header_line[: len(header_line) - len(header_line.lstrip())]
                out[idx] = f"{indent_str}{section}: {empty}{eol}"

    if not changed:
        return False, "config already clean"

    backup = config_path.with_name(config_path.name + ".cache-meter-uninstall.bak")
    backup.write_bytes(original.encode("utf-8"))
    with open(config_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(out))
    return True, f"removed {removed} (backup: {backup.name})"


def hermes_running() -> bool:
    """Best-effort check for a running desktop app / gateway (Windows)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Hermes.exe"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.lower()
        return "hermes.exe" in out
    except Exception:
        return False


def main() -> int:
    # Leave the package directory: a process cwd inside the folder blocks its
    # rename on Windows (that is one of the locks `hermes plugins remove` hits).
    os.chdir(tempfile.gettempdir())

    home = find_hermes_home()
    if home is None:
        print("Could not locate the Hermes home directory.")
        print("Set HERMES_HOME and run this script again.")
        return 1

    plugins_dir = home / "plugins"
    chip_dir = home / "desktop-plugins" / PLUGIN_ID
    pkg_dir = plugins_dir / PLUGIN_ID
    print(f"Hermes home: {home}")

    # The script lives inside the package it deletes. Park a copy in temp so a
    # "quit Hermes and run again" rerun still has a script to run.
    rerun_copy: Path | None = None
    try:
        rerun_copy = Path(tempfile.gettempdir()) / "cache-meter-uninstall.py"
        shutil.copy(__file__, rerun_copy)
    except OSError:
        rerun_copy = None

    if hermes_running():
        print()
        print("NOTE: Hermes is currently running. Its folder watchers can hold")
        print("handles inside the installed plugin, so a full uninstall may need")
        print("a second run after quitting Hermes. Continuing...")

    # 1) Desktop chip copy.
    if chip_dir.exists():
        print("Removing desktop chip copy...", end=" ", flush=True)
        print("done" if retry_rmtree(chip_dir) else f"FAILED (delete manually: {chip_dir})")
    else:
        print("Desktop chip copy: already absent")

    # 2) Agent package: prefer Hermes' own remover, fall back to a retrying rename.
    #    (Before the ghost sweep, so staging dirs this step creates get swept below.)
    failed_agent = False
    if pkg_dir.is_dir():
        print("Removing agent package via hermes CLI...", end=" ", flush=True)
        ok, detail = remove_agent_via_cli()
        if ok and not pkg_dir.is_dir():
            print("done")
        else:
            ok2, msg = manual_remove_agent(plugins_dir)
            if ok2:
                suffix = f" (CLI said: {detail})" if detail else ""
                print(f"done ({msg}){suffix}")
            else:
                failed_agent = True
                print(f"FAILED: {msg}")
                print()
                print("The agent folder is locked by the running Hermes gateway.")
                print("Quit Hermes completely, then run this script once more.")
    else:
        print("Agent package: already absent")

    # 3) Ghost staging dirs left by failed removes/installs (they show up as
    #    phantom plugin rows in Settings that cannot be toggled).
    ghosts = sorted(plugins_dir.glob(f".{PLUGIN_ID}.remove-*")) + sorted(
        plugins_dir.glob(f".{PLUGIN_ID}.uninstall-*")
    )
    # Generic install staging (.install-<random>) is shared across plugins and
    # may belong to a LIVE install of something else - only take dirs that
    # verifiably contain a cache-meter copy.
    for candidate in sorted(plugins_dir.glob(".install-*")):
        if not candidate.is_dir():
            continue
        marker_hit = False
        try:
            for probe in candidate.rglob("plugin.yaml"):
                try:
                    if PLUGIN_ID in probe.read_text(encoding="utf-8", errors="ignore")[:2000]:
                        marker_hit = True
                        break
                except OSError:
                    continue
            if not marker_hit:
                for probe in candidate.rglob(".git"):
                    cfg = probe / "config"
                    if cfg.is_file() and PLUGIN_ID in cfg.read_text(encoding="utf-8", errors="ignore"):
                        marker_hit = True
                        break
        except OSError:
            continue
        if marker_hit:
            ghosts.append(candidate)
    for ghost in ghosts:
        print(f"Removing leftover staging dir {ghost.name}...", end=" ", flush=True)
        print("done" if retry_rmtree(ghost) else "FAILED (locked; delete after quitting Hermes)")

    # 4) config.yaml: drop real + phantom cache-meter entries.
    changed, msg = clean_config(home / "config.yaml")
    print(f"config.yaml: {msg}")

    remaining: list[str] = []
    if pkg_dir.exists():
        remaining.append("agent package folder (plugins/cache-meter)")
    remaining.extend(ghost.name for ghost in ghosts if ghost.exists())

    if remaining:
        print()
        print("Uninstall INCOMPLETE. Still present:")
        for item in remaining:
            print(f"  - {item}")
        print()
        print("Quit Hermes completely (including the tray icon), then run this")
        print("script once more: it skips everything already removed and")
        print("finishes the job.")
        if rerun_copy is not None and rerun_copy.exists():
            print()
            print("Rerun command (the copy parked in your temp folder):")
            print(f'  python "{rerun_copy}"')
        return 1

    print()
    print("Uninstall complete. The status-bar chip disappears within a few")
    print("seconds, or instantly after reopening the desktop app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
