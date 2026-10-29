#!/usr/bin/env python3
"""
Paradox Mod Manager — offline, no login, no Paradox launcher required.

Supported games
  * Classic descriptor games (descriptor.mod + dlc_load.json):
      Crusader Kings III, Europa Universalis IV, Hearts of Iron IV, Stellaris, Imperator: Rome
  * Victoria 3 (.metadata/metadata.json mods + content_load.json)
  * Europa Universalis V (.metadata/metadata.json mods + the in-game playsets.json)

Features
  * Pick the game install, executable and user-data folder yourself (Steam detection optional).
  * Extra "mod library" folders anywhere on disk; Steam Workshop downloads picked up directly.
  * Redirect the save-games folder anywhere (folder link), or move all user data (userdir.txt).
  * Shows game version, each mod's version and the game version it targets, with a verdict.
  * Turn individual DLC on/off.
  * Load order: drag-and-drop, up/down/top/bottom, sort-by-dependencies, file-conflict checker,
    pre-launch problem check, playsets, export/import, undo last save.
  * Save-game backups, dark mode, remembers window layout.
  * Updates itself from GitHub releases.

Requires Python 3.8+ (Tkinter is included with the python.org Windows installer).
Run:  python paradox_mod_manager.py
"""

__version__ = "2.0.0"
UPDATE_REPO = "gbesh/paradox-mod-manager"
APP_NAME = "Paradox Mod Manager"

import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
except ImportError:  # lets the logic be imported/tested without Tk
    tk = None

MARKER = "# pmm-managed"  # first line of descriptors this app writes (only these are ever auto-deleted)

# format: classic = descriptor.mod + dlc_load.json, content_load = Victoria 3, playsets = EU5
GAMES = {
    "ck3": {"name": "Crusader Kings III", "user_folder": "Crusader Kings III", "format": "classic",
            "exe": ["binaries/ck3.exe", "binaries/ck3", "ck3"], "steam_dir": "Crusader Kings III",
            "appid": "1158310"},
    "eu4": {"name": "Europa Universalis IV", "user_folder": "Europa Universalis IV", "format": "classic",
            "exe": ["eu4.exe", "eu4"], "steam_dir": "Europa Universalis IV", "appid": "236850"},
    "eu5": {"name": "Europa Universalis V", "user_folder": "Europa Universalis V", "format": "playsets",
            "exe": ["binaries/eu5.exe", "binaries/eu5"], "steam_dir": "Europa Universalis V",
            "appid": "3450310"},
    "hoi4": {"name": "Hearts of Iron IV", "user_folder": "Hearts of Iron IV", "format": "classic",
             "exe": ["hoi4.exe", "hoi4"], "steam_dir": "Hearts of Iron IV", "appid": "394360"},
    "imperator": {"name": "Imperator: Rome", "user_folder": "Imperator", "format": "classic",
                  "exe": ["binaries/imperator.exe", "binaries/imperator"], "steam_dir": "ImperatorRome",
                  "appid": "859580"},
    "stellaris": {"name": "Stellaris", "user_folder": "Stellaris", "format": "classic",
                  "exe": ["stellaris.exe", "stellaris"], "steam_dir": "Stellaris", "appid": "281990"},
    "vic3": {"name": "Victoria 3", "user_folder": "Victoria 3", "format": "content_load",
             "exe": ["binaries/victoria3.exe", "binaries/victoria3"], "steam_dir": "Victoria 3",
             "appid": "529340"},
}

# files that never count as conflicts between mods
CONFLICT_IGNORE = re.compile(r"(^|/)(descriptor\.mod|thumbnail\.\w+|\.metadata/.*|readme.*|changelog.*|license.*"
                             r"|.*\.md|\.git/.*|\.gitignore|.*\.mod)$", re.I)


# ============================================================================ config

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0] or __file__))


def config_path():
    here = app_dir()
    if os.access(here, os.W_OK):
        return os.path.join(here, "pmm_config.json")
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "ParadoxModManager")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "pmm_config.json")


def default_game_cfg():
    return {"install_dir": "", "exe": "", "user_dir": "", "version_override": "",
            "launch_args": "", "libraries": [], "scan_workshop": True,
            "saves_dir": "", "playsets": {}, "pre_launch_check": True}


def default_ui_cfg():
    return {"geometry": "", "dark": False, "auto_update": True, "skip_version": "", "last_tab": 0,
            "update_repo": UPDATE_REPO}


def load_config():
    cfg = {"current_game": "ck3", "games": {}, "ui": {}}
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    for k in GAMES:
        g = default_game_cfg()
        g.update(cfg["games"].get(k, {}))
        cfg["games"][k] = g
    ui = default_ui_cfg()
    ui.update(cfg.get("ui") or {})
    cfg["ui"] = ui
    if cfg.get("current_game") not in GAMES:
        cfg["current_game"] = "ck3"
    return cfg


def save_config(cfg):
    p = config_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)


# ============================================================================ paths

def norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""


def fwd(p):
    return os.path.normpath(p).replace("\\", "/") if p else p


def documents_dir():
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def read_userdir_txt(install_dir):
    p = os.path.join(install_dir, "userdir.txt") if install_dir else ""
    if p and os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                for line in f:
                    if line.strip():
                        return line.strip().strip('"')
        except OSError:
            pass
    return ""


def default_user_dir(game_key, install_dir=""):
    custom = read_userdir_txt(install_dir)
    if custom:
        return custom
    folder = GAMES[game_key]["user_folder"]
    if sys.platform.startswith("linux"):
        return os.path.join(os.path.expanduser("~"), ".local", "share", "Paradox Interactive", folder)
    return os.path.join(documents_dir(), "Paradox Interactive", folder)


def steam_roots():
    roots = []
    if os.name == "nt":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, name)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    elif sys.platform == "darwin":
        roots.append(os.path.expanduser("~/Library/Application Support/Steam"))
    else:
        roots += [os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam"),
                  os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam")]
    libs = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as f:
                for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                    libs.append(m.group(1).replace("\\\\", "\\"))
        except OSError:
            pass
    seen, out = set(), []
    for l in libs:
        if norm(l) not in seen:
            seen.add(norm(l))
            out.append(l)
    return out


def detect_install(game_key):
    for lib in steam_roots():
        p = os.path.join(lib, "steamapps", "common", GAMES[game_key]["steam_dir"])
        if os.path.isdir(p):
            return p
    return ""


def detect_exe(game_key, install_dir):
    if not install_dir:
        return ""
    for rel in GAMES[game_key]["exe"]:
        p = os.path.join(install_dir, *rel.split("/"))
        if os.path.isfile(p):
            return p
    return ""


def workshop_dir(game_key, install_dir):
    """<library>/steamapps/common/<game>  ->  <library>/steamapps/workshop/content/<appid>"""
    if not install_dir:
        return ""
    p = os.path.normpath(os.path.join(install_dir, "..", "..", "workshop", "content", GAMES[game_key]["appid"]))
    return p if os.path.isdir(p) else ""


def read_json(p, default=None):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(p, data, indent=1, backup=True):
    if backup and os.path.isfile(p):
        shutil.copy2(p, p + ".bak")
    tmp = p + ".pmmtmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, p)


def launcher_settings(install_dir):
    if not install_dir:
        return {}
    for rel in (("launcher", "launcher-settings.json"), ("launcher-settings.json",)):
        j = read_json(os.path.join(install_dir, *rel))
        if isinstance(j, dict):
            return j
    return {}


def detect_game_version(install_dir, user_dir=""):
    """Returns (raw_version, display_version). Uses launcher-settings.json, falling back to the
    version stamped in the user folder's continue_game.json (EU5 ships no launcher settings)."""
    j = launcher_settings(install_dir)
    raw = str(j.get("rawVersion") or j.get("version") or "")
    if raw:
        return raw, str(j.get("version") or raw)
    if user_dir:
        c = read_json(os.path.join(user_dir, "continue_game.json"))
        if isinstance(c, dict) and c.get("rawGameVersion"):
            v = str(c["rawGameVersion"])
            return v, f"{v} (last played)"
    return "", ""


# ============================================================================ descriptor parsing

def tokenize(text):
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{}=":
            toks.append(("p", c))
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            if j == -1:
                j = n
            toks.append(("s", text[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}="#':
                j += 1
            toks.append(("s", text[i:j]))
            i = j
    return toks


def parse_descriptor(text):
    toks, d, i = tokenize(text), {}, 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "s" and i + 2 < len(toks) and toks[i + 1] == ("p", "="):
            key, v = t[1].lower(), toks[i + 2]
            if v == ("p", "{"):
                depth, j, items = 1, i + 3, []
                while j < len(toks) and depth > 0:
                    if toks[j] == ("p", "{"):
                        depth += 1
                    elif toks[j] == ("p", "}"):
                        depth -= 1
                    elif toks[j][0] == "s" and depth == 1:
                        items.append(toks[j][1])
                    j += 1
                d[key] = items
                i = j
            elif v[0] == "s":
                d[key] = v[1]
                i += 3
            else:
                i += 1
        else:
            i += 1
    return d


def read_text(p):
    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def as_str(v):
    return " ".join(v) if isinstance(v, list) else ("" if v is None else str(v))


def as_list(v):
    if isinstance(v, list):
        return v
    return [v] if v else []


def base_name(path):
    """Last path component, splitting on both / and \\ (load files may hold Windows paths)."""
    parts = [x for x in re.split(r"[\\/]", path or "") if x]
    return parts[-1] if parts else (path or "")


def leading_workshop_id(folder):
    m = re.match(r"^(\d{6,})(\b|_|\s)", os.path.basename(os.path.normpath(folder)))
    return m.group(1) if m else ""


# ============================================================================ versions

def compat_status(supported, game_raw):
    """-> (kind, label). kind in ok / minor / old / new / unknown"""
    supported = (supported or "").strip()
    if not supported:
        return "unknown", "? Not stated"
    if supported in ("*", "*.*", "*.*.*"):
        return "ok", "✔ Any version"
    gm = re.search(r"\d+(?:\.\d+)*", game_raw or "")
    if not gm:
        return "unknown", "? Game version unknown"
    sm = re.search(r"\d+(?:\.(?:\d+|\*))*", supported)
    if not sm:
        return "unknown", "? Unreadable"
    gv = [int(x) for x in gm.group(0).split(".")]
    for i, p in enumerate(sm.group(0).split(".")):
        if p == "*" or i >= len(gv):
            break
        pv = int(p)
        if pv != gv[i]:
            if i < 2:
                return ("old", "✖ Outdated") if pv < gv[i] else ("new", "✖ Needs newer game")
            return "minor", "≈ Different patch"
    return "ok", "✔ Compatible"


def parse_ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:4])


def is_newer(remote, local):
    return parse_ver(remote) > parse_ver(local)


# ============================================================================ mod records

def new_mod(mid, **kw):
    m = {"id": mid, "file": "", "name": mid, "version": "", "supported": "", "deps": [], "tags": [],
         "path": "", "exists": False, "source": "Missing", "managed": False, "remote_id": "", "meta_id": ""}
    m.update(kw)
    return m


# ---------------------------------------------------------------------------- classic (.mod) games

def library_candidates(folder, marker=("descriptor.mod",)):
    """A library folder may be a mod itself, or contain mods as subfolders."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    if os.path.isfile(os.path.join(folder, *marker)):
        return [folder]
    try:
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, *marker)):
                out.append(p)
    except OSError:
        pass
    return out


def managed_filename(mod_path):
    base = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(os.path.normpath(mod_path))).strip("_")[:40] or "mod"
    h = hashlib.md5(norm(mod_path).encode("utf-8")).hexdigest()[:8]
    return f"pmm_{base}_{h}.mod"


def build_managed_descriptor(mod_path):
    src = read_text(os.path.join(mod_path, "descriptor.mod"))
    body = re.sub(r"(?im)^\s*(path|archive)\s*=.*$\n?", "", src).strip()
    return (f"{MARKER} — generated by {APP_NAME}; edit the mod's own descriptor.mod instead.\n"
            f"{body}\npath=\"{fwd(os.path.abspath(mod_path))}\"\n")


def is_managed_file(p):
    try:
        with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.readline().startswith(MARKER)
    except OSError:
        return False


def sync_libraries(user_dir, libraries):
    """Write .mod descriptors into <user_dir>/mod for every mod found in library folders,
    skipping mods already registered by another descriptor. Removes stale managed descriptors."""
    moddir = os.path.join(user_dir, "mod")
    os.makedirs(moddir, exist_ok=True)
    referenced, managed_existing = set(), set()
    for fn in os.listdir(moddir):
        if not fn.lower().endswith(".mod"):
            continue
        fp = os.path.join(moddir, fn)
        if is_managed_file(fp):
            managed_existing.add(fn)
            continue
        try:
            d = parse_descriptor(read_text(fp))
        except OSError:
            continue
        p = as_str(d.get("path")) or as_str(d.get("archive"))
        if p:
            if not os.path.isabs(p):
                p = os.path.join(user_dir, p)
            referenced.add(norm(p))
    wanted, errors = set(), []
    for lib in libraries:
        for mp in library_candidates(lib):
            if norm(mp) in referenced or norm(mp).startswith(norm(moddir) + os.sep):
                continue
            fn = managed_filename(mp)
            if fn in wanted:
                continue
            wanted.add(fn)
            try:
                content = build_managed_descriptor(mp)
                fp = os.path.join(moddir, fn)
                old = read_text(fp) if os.path.isfile(fp) else None
                if old != content:
                    with open(fp, "w", encoding="utf-8") as f:
                        f.write(content)
            except OSError as e:
                errors.append(f"{mp}: {e}")
    for fn in managed_existing - wanted:
        try:
            os.remove(os.path.join(moddir, fn))
        except OSError as e:
            errors.append(f"{fn}: {e}")
    return errors


def scan_classic_mods(user_dir, workshop=""):
    mods = {}
    moddir = os.path.join(user_dir, "mod")
    if not os.path.isdir(moddir):
        return mods
    for fn in sorted(os.listdir(moddir)):
        if not fn.lower().endswith(".mod"):
            continue
        fp = os.path.join(moddir, fn)
        try:
            text = read_text(fp)
        except OSError:
            continue
        d = parse_descriptor(text)
        path = as_str(d.get("path")) or as_str(d.get("archive"))
        abspath = path if (not path or os.path.isabs(path)) else os.path.join(user_dir, path)
        if abspath:
            abspath = fwd(abspath)
        inner = {}
        if abspath and os.path.isdir(abspath) and os.path.isfile(os.path.join(abspath, "descriptor.mod")):
            try:
                inner = parse_descriptor(read_text(os.path.join(abspath, "descriptor.mod")))
            except OSError:
                pass

        def pick(k):
            return as_str(d.get(k)) or as_str(inner.get(k))

        np_ = norm(abspath)
        managed = text.lstrip().startswith(MARKER)
        if "archive" in d and not d.get("path"):
            source = "Archive"
        elif (workshop and np_.startswith(norm(workshop))) or ("workshop" + os.sep + "content") in np_:
            source = "Workshop"
        elif managed:
            source = "Library"
        else:
            source = "Local"
        mid = "mod/" + fn
        mods[mid] = new_mod(
            mid, file=fp, name=pick("name") or fn, version=pick("version"),
            supported=pick("supported_version"),
            deps=as_list(d.get("dependencies") or inner.get("dependencies")),
            tags=as_list(d.get("tags") or inner.get("tags")),
            path=abspath, exists=bool(abspath) and os.path.exists(abspath), source=source, managed=managed,
            remote_id=pick("remote_file_id") or leading_workshop_id(abspath or ""))
    return mods


# ---------------------------------------------------------------------------- metadata (.metadata/metadata.json) games

def read_metadata(folder):
    j = read_json(os.path.join(folder, ".metadata", "metadata.json"))
    return j if isinstance(j, dict) else None


def metadata_deps(md):
    out = []
    for r in md.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        rel = str(r.get("rel_type") or r.get("relationship_type") or "dependency").lower()
        if rel not in ("dependency", "required", "requires"):
            continue
        name = r.get("display_name") or r.get("id")
        if name:
            out.append(str(name))
    return out


def metadata_mod(path, source):
    md = read_metadata(path) or {}
    remote = leading_workshop_id(path) if source != "Workshop" else os.path.basename(os.path.normpath(path))
    return new_mod(norm(path), file=os.path.join(path, ".metadata", "metadata.json"),
                   name=as_str(md.get("name")) or os.path.basename(os.path.normpath(path)),
                   version=as_str(md.get("version")), supported=as_str(md.get("supported_game_version")),
                   deps=metadata_deps(md), tags=as_list(md.get("tags")), path=fwd(path),
                   exists=os.path.isdir(path), source=source, remote_id=remote if remote.isdigit() else "",
                   meta_id=as_str(md.get("id")))


def scan_metadata_mods(roots):
    """roots: list of (folder, source). Returns {norm(path): mod}."""
    mods = {}
    for folder, source in roots:
        for mp in library_candidates(folder, (".metadata", "metadata.json")):
            mid = norm(mp)
            if mid not in mods:
                mods[mid] = metadata_mod(mp, source)
    return mods


# ============================================================================ DLC discovery

def dlc_root(install_dir):
    """CK3 / Imperator / Vic3 / EU5 keep DLC in <install>/game/dlc, EU4 / HOI4 / Stellaris in <install>/dlc."""
    if not install_dir:
        return ""
    for rel in (("game", "dlc"), ("dlc",)):
        p = os.path.join(install_dir, *rel)
        if os.path.isdir(p):
            return p
    return ""


def scan_classic_dlcs(install_dir):
    """[{id, name, steam_id, file}] where id is what dlc_load.json uses, e.g. 'dlc/dlc016_cp2/dlc016.dlc'."""
    root = dlc_root(install_dir)
    out = []
    if not root:
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        depth = 0 if dirpath == root else os.path.relpath(dirpath, root).count(os.sep) + 1
        if depth >= 2:
            dirnames[:] = []
        for fn in filenames:
            if not fn.lower().endswith(".dlc"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                d = parse_descriptor(read_text(fp))
            except OSError:
                d = {}
            out.append({"id": "dlc/" + os.path.relpath(fp, root).replace("\\", "/"), "file": fp,
                        "name": as_str(d.get("name")) or as_str(d.get("localizable_name")) or fn,
                        "steam_id": as_str(d.get("steam_id")), "hidden": False})
    out.sort(key=lambda x: x["id"].lower())
    return out


def pretty_dlc_name(s):
    s = re.sub(r"^[dD]\d{3}_|^dlc\d{3}_", "", s or "")
    return s.replace("_", " ").strip().title() or s


def scan_json_dlcs(install_dir):
    """Victoria 3 / EU5 '*.dlc.json'. id = what content_load.json / playsets.json use:
    Vic3 -> id.paradox, EU5 -> name."""
    root = dlc_root(install_dir)
    out = []
    if not root:
        return out
    try:
        folders = sorted(os.listdir(root))
    except OSError:
        return out
    for fol in folders:
        fdir = os.path.join(root, fol)
        if not os.path.isdir(fdir):
            continue
        for fn in os.listdir(fdir):
            if not fn.lower().endswith(".dlc.json"):
                continue
            j = read_json(os.path.join(fdir, fn))
            if not isinstance(j, dict):
                continue
            ids = j.get("id") if isinstance(j.get("id"), dict) else {}
            app_id = as_str(ids.get("paradox")) or as_str(j.get("name")) or fol
            disp = j.get("displayName")
            name = (as_str(disp.get("en")) if isinstance(disp, dict) else "") or pretty_dlc_name(as_str(j.get("name")) or fol)
            steam = as_str(ids.get("steam")) or as_str(j.get("steam_id"))
            out.append({"id": app_id, "name": name, "steam_id": "" if steam == "0" else steam,
                        "file": os.path.join(fdir, fn), "folder": fol, "hidden": bool(j.get("hidden"))})
            break
    return out


def fuzzy_dlc_match(label, dlc):
    """EU5 playsets written by older game versions name DLC by display name ('Sacred Sites')."""
    if not label:
        return False
    if label == dlc["id"] or label.lower() == dlc["name"].lower():
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", label.lower()) if len(w) > 2]
    hay = (dlc.get("folder", "") + " " + dlc["id"]).lower()
    return bool(words) and all(w in hay or w.rstrip("s") in hay for w in words)


# ============================================================================ backends

class Backend:
    """Loads and saves one game's mod list + DLC state. State dict:
       mods {id: mod}, enabled [ids in load order], dlcs [{id,name,...}], disabled set(dlc ids)"""
    load_file = ""
    supports_userdir_txt = True
    marker = ("descriptor.mod",)

    def __init__(self, key):
        self.key = key

    def load_path(self, user_dir):
        return os.path.join(user_dir, self.load_file)

    def backup_path(self, user_dir):
        return self.load_path(user_dir) + ".bak"


class ClassicBackend(Backend):
    load_file = "dlc_load.json"

    def load(self, g, user_dir):
        install = g["install_dir"]
        ws = workshop_dir(self.key, install)
        libs = list(g["libraries"])
        if g.get("scan_workshop", True) and ws:
            libs.append(ws)
        errors = []
        if os.path.isdir(user_dir):
            try:
                errors = sync_libraries(user_dir, libs)
            except OSError as e:
                errors = [str(e)]
        mods = scan_classic_mods(user_dir, ws)
        data = read_json(self.load_path(user_dir), {}) or {}
        enabled = [m for m in data.get("enabled_mods", []) if isinstance(m, str)]
        for mid in enabled:
            if mid not in mods:
                mods[mid] = new_mod(mid)
        return {"mods": mods, "enabled": enabled, "dlcs": scan_classic_dlcs(install),
                "disabled": {d for d in data.get("disabled_dlcs", []) if isinstance(d, str)},
                "errors": errors, "note": ""}

    def save(self, user_dir, state):
        p = self.load_path(user_dir)
        data = read_json(p, {}) or {}
        if not isinstance(data, dict):
            data = {}
        data["enabled_mods"] = list(state["enabled"])
        data["disabled_dlcs"] = sorted(state["disabled"])
        write_json(p, data)


class MetadataBackend(Backend):
    supports_userdir_txt = False
    marker = (".metadata", "metadata.json")

    def scan(self, g, user_dir):
        install = g["install_dir"]
        roots = [(os.path.join(user_dir, "mod"), "Local")]
        roots += [(lib, "Library") for lib in g["libraries"]]
        ws = workshop_dir(self.key, install)
        if g.get("scan_workshop", True) and ws:
            roots.append((ws, "Workshop"))
        return scan_metadata_mods(roots)

    def ensure(self, mods, path):
        """Return mod id for a load-file path, creating a Missing/extra record if needed."""
        mid = norm(path)
        if mid not in mods:
            if os.path.isdir(path) and read_metadata(path) is not None:
                mods[mid] = metadata_mod(path, "Other")
            else:
                mods[mid] = new_mod(mid, name=base_name(path), path=fwd(path))
        return mid

    @staticmethod
    def native(path):
        return os.path.normpath(path)


class ContentLoadBackend(MetadataBackend):
    """Victoria 3: content_load.json {"disabledDLC":[{"paradoxAppId":..}], "enabledMods":[{"path":..}], ...}"""
    load_file = "content_load.json"

    def load(self, g, user_dir):
        mods = self.scan(g, user_dir)
        data = read_json(self.load_path(user_dir), {}) or {}
        enabled = []
        for e in data.get("enabledMods", []) or []:
            p = e.get("path") if isinstance(e, dict) else None
            if p:
                mid = self.ensure(mods, p)
                if mid not in enabled:
                    enabled.append(mid)
        dlcs = [d for d in scan_json_dlcs(g["install_dir"]) if not d["hidden"]]
        disabled = {as_str(d.get("paradoxAppId")) for d in data.get("disabledDLC", []) or []
                    if isinstance(d, dict) and d.get("paradoxAppId")}
        return {"mods": mods, "enabled": enabled, "dlcs": dlcs, "disabled": disabled, "errors": [], "note": ""}

    def save(self, user_dir, state):
        p = self.load_path(user_dir)
        data = read_json(p, {}) or {}
        if not isinstance(data, dict):
            data = {}
        data["enabledMods"] = [{"path": self.native(state["mods"][m]["path"] or m)} for m in state["enabled"]]
        data["disabledDLC"] = [{"paradoxAppId": d} for d in sorted(state["disabled"])]
        write_json(p, data)


class PlaysetsBackend(MetadataBackend):
    """EU5: playsets.json — edits the playset marked isActive (the one the game loads)."""
    load_file = "playsets.json"

    @staticmethod
    def active(data):
        ps = data.get("playsets") if isinstance(data, dict) else None
        if not isinstance(ps, list) or not ps:
            return None
        for p in ps:
            if isinstance(p, dict) and p.get("isActive"):
                return p
        return ps[0] if isinstance(ps[0], dict) else None

    def read(self, user_dir):
        data = read_json(self.load_path(user_dir))
        if not isinstance(data, dict) or self.active(data) is None:
            data = {"file_version": "1.0.0", "playsets": [{
                "name": "Paradox Mod Manager", "isActive": True, "isAutomaticallySorted": False,
                "orderedListMods": [], "DLC": []}]}
        return data

    def load(self, g, user_dir):
        mods = self.scan(g, user_dir)
        data = self.read(user_dir)
        ps = self.active(data)
        enabled = []
        for e in ps.get("orderedListMods") or []:
            if isinstance(e, dict) and e.get("path"):
                mid = self.ensure(mods, e["path"])
                if e.get("isEnabled") and mid not in enabled:
                    enabled.append(mid)
        installed = scan_json_dlcs(g["install_dir"])
        dlcs, disabled, used = [], set(), set()
        for e in ps.get("DLC") or []:
            if not isinstance(e, dict) or not e.get("paradoxAppId"):
                continue
            label = as_str(e["paradoxAppId"])
            match = next((d for d in installed if d["id"] not in used and fuzzy_dlc_match(label, d)), None)
            if match:
                used.add(match["id"])
                if match["hidden"]:
                    continue
                item = dict(match, id=label)
            else:
                item = {"id": label, "name": label, "steam_id": "", "file": "", "hidden": False}
            dlcs.append(item)
            if e.get("isEnabled") is False:
                disabled.add(label)
        for d in installed:
            if d["id"] not in used and not d["hidden"]:
                dlcs.append(d)
        note = f"Editing EU5 playset '{ps.get('name', '?')}'."
        return {"mods": mods, "enabled": enabled, "dlcs": dlcs, "disabled": disabled, "errors": [], "note": note}

    def save(self, user_dir, state):
        data = self.read(user_dir)
        ps = self.active(data)
        old = [e for e in (ps.get("orderedListMods") or []) if isinstance(e, dict) and e.get("path")]
        by_id = {norm(e["path"]): e for e in old}
        new_list = []
        for mid in state["enabled"]:
            e = dict(by_id.get(mid, {}))
            e["path"] = e.get("path") or self.native(state["mods"][mid]["path"] or mid)
            e["isEnabled"] = True
            new_list.append(e)
        for e in old:  # keep previously listed mods, switched off
            if norm(e["path"]) not in state["enabled"]:
                e = dict(e)
                e["isEnabled"] = False
                new_list.append(e)
        ps["orderedListMods"] = new_list
        if state["enabled"]:
            ps["isAutomaticallySorted"] = False  # otherwise the game re-sorts our order
        entries = [e for e in (ps.get("DLC") or []) if isinstance(e, dict)]
        known = {as_str(e.get("paradoxAppId")) for e in entries}
        for e in entries:
            if as_str(e.get("paradoxAppId")) in {d["id"] for d in state["dlcs"]}:
                e["isEnabled"] = as_str(e.get("paradoxAppId")) not in state["disabled"]
        for d in state["dlcs"]:
            if d["id"] not in known and d["id"] in state["disabled"]:
                entries.append({"paradoxAppId": d["id"], "isEnabled": False})
        ps["DLC"] = entries
        os.makedirs(user_dir, exist_ok=True)
        write_json(self.load_path(user_dir), data, indent="\t")


def make_backend(key):
    fmt = GAMES[key]["format"]
    if fmt == "content_load":
        return ContentLoadBackend(key)
    if fmt == "playsets":
        return PlaysetsBackend(key)
    return ClassicBackend(key)


# ============================================================================ load-order helpers

def name_index(mods, ids):
    """Map every name / metadata id of the given mods to their position in ids."""
    idx = {}
    for i, mid in enumerate(ids):
        m = mods.get(mid)
        if not m:
            continue
        for k in (m["name"], m.get("meta_id"), m.get("remote_id")):
            if k:
                idx.setdefault(k.lower(), i)
    return idx


def dependency_problems(mods, enabled):
    """{mod_id: [problem strings]} for missing / misordered dependencies among enabled mods."""
    idx = name_index(mods, enabled)
    out = {}
    for pos, mid in enumerate(enabled):
        m = mods.get(mid)
        if not m:
            continue
        probs = []
        for dep in m["deps"]:
            j = idx.get(dep.lower())
            if j is None:
                probs.append(f"needs '{dep}'")
            elif j > pos:
                probs.append(f"load after '{dep}'")
        if probs:
            out[mid] = probs
    return out


def sort_by_dependencies(mods, enabled):
    """Stable reorder so every enabled dependency loads before the mod needing it."""
    idx = name_index(mods, enabled)
    result, state = [], {}

    def visit(mid):
        if state.get(mid) == 2:
            return
        if state.get(mid) == 1:  # cycle — leave as is
            return
        state[mid] = 1
        for dep in mods.get(mid, {}).get("deps", []):
            j = idx.get(dep.lower())
            if j is not None:
                visit(enabled[j])
        state[mid] = 2
        result.append(mid)

    for mid in enabled:
        visit(mid)
    return result


def mod_files(path, limit=200000):
    """Relative file paths of a mod folder (lower-case, forward slashes), minus non-conflicting files."""
    out = set()
    if not path or not os.path.isdir(path):
        return out
    base = os.path.normpath(path)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), base).replace("\\", "/").lower()
            if not CONFLICT_IGNORE.search(rel):
                out.add(rel)
                if len(out) >= limit:
                    return out
    return out


def find_conflicts(mods, enabled):
    """-> (summary {mid: {"wins": n, "loses": n}}, files {rel: [mids in load order]}) for files in 2+ mods.
    Later mods in the load order win."""
    owners = {}
    for mid in enabled:
        m = mods.get(mid)
        if not m or not m["exists"]:
            continue
        for rel in mod_files(m["path"]):
            owners.setdefault(rel, []).append(mid)
    files = {rel: ids for rel, ids in owners.items() if len(ids) > 1}
    summary = {mid: {"wins": 0, "loses": 0} for mid in enabled}
    for ids in files.values():
        summary[ids[-1]]["wins"] += 1
        for mid in ids[:-1]:
            summary[mid]["loses"] += 1
    return summary, files


def launch_problems(mods, enabled, game_raw):
    out = []
    deps = dependency_problems(mods, enabled)
    for mid in enabled:
        m = mods.get(mid) or new_mod(mid)
        if m["source"] == "Missing":
            out.append(f"{m['name']}: not installed / descriptor missing")
            continue
        if not m["exists"]:
            out.append(f"{m['name']}: mod files not found at {m['path']}")
        for p in deps.get(mid, []):
            out.append(f"{m['name']}: {p}")
        kind, label = compat_status(m["supported"], game_raw)
        if kind in ("old", "new"):
            out.append(f"{m['name']}: {label.strip('✖ ')} (made for {m['supported']}, game is {game_raw})")
    return out


# ============================================================================ export / import

def export_load_order(key, mods, enabled, disabled):
    return {"app": "paradox-mod-manager", "app_version": __version__, "game": key,
            "exported": datetime.datetime.now().isoformat(timespec="seconds"),
            "mods": [{"name": mods[m]["name"], "id": m, "path": mods[m]["path"],
                      "remote_id": mods[m]["remote_id"], "version": mods[m]["version"]} for m in enabled],
            "disabled_dlcs": sorted(disabled)}


def import_load_order(data, mods):
    """-> (enabled ids, not_found names). Matches by id, then Workshop id, then path, then name."""
    by_remote = {m["remote_id"]: k for k, m in mods.items() if m["remote_id"] and m["source"] != "Missing"}
    by_path = {norm(m["path"]): k for k, m in mods.items() if m["path"] and m["source"] != "Missing"}
    by_name = {m["name"].lower(): k for k, m in mods.items() if m["source"] != "Missing"}
    enabled, missing = [], []
    for e in data.get("mods", []):
        mid = None
        if e.get("id") in mods and mods[e["id"]]["source"] != "Missing":
            mid = e["id"]
        elif e.get("remote_id") and e["remote_id"] in by_remote:
            mid = by_remote[e["remote_id"]]
        elif e.get("path") and norm(e["path"]) in by_path:
            mid = by_path[norm(e["path"])]
        elif e.get("name", "").lower() in by_name:
            mid = by_name[e["name"].lower()]
        if mid and mid not in enabled:
            enabled.append(mid)
        elif not mid:
            missing.append(e.get("name") or e.get("id") or "?")
    return enabled, missing


# ============================================================================ save folder redirect / backup

def is_link(p):
    if os.path.islink(p):
        return True
    if hasattr(os.path, "isjunction") and os.path.isjunction(p):
        return True
    if os.name == "nt" and os.path.exists(p):
        try:
            return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except (OSError, AttributeError):
            pass
    return False


def remove_link(p):
    try:
        os.unlink(p)
    except OSError:
        os.rmdir(p)  # removes a Windows junction without touching its target


def make_dir_link(link, target):
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except OSError:
            raise OSError((r.stderr or r.stdout or "mklink failed").strip())
    os.symlink(target, link, target_is_directory=True)


def unique_dest(dst):
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def redirect_saves(user_dir, target):
    """Point <user_dir>/save games at target. Existing saves are MOVED to target (never deleted)."""
    link = os.path.join(user_dir, "save games")
    target = os.path.abspath(target)
    if norm(target) == norm(link):
        raise OSError("Target is the default save folder itself.")
    os.makedirs(user_dir, exist_ok=True)
    os.makedirs(target, exist_ok=True)
    moved = 0
    if is_link(link):
        remove_link(link)
    elif os.path.isdir(link):
        for name in os.listdir(link):
            shutil.move(os.path.join(link, name), unique_dest(os.path.join(target, name)))
            moved += 1
        os.rmdir(link)
    make_dir_link(link, target)
    return moved


def undo_saves_redirect(user_dir):
    """Remove the link and restore a real folder, COPYING saves back (the custom folder is left intact)."""
    link = os.path.join(user_dir, "save games")
    if not is_link(link):
        return 0
    target = os.path.realpath(link)
    remove_link(link)
    os.makedirs(link, exist_ok=True)
    copied = 0
    if os.path.isdir(target):
        for name in os.listdir(target):
            s = os.path.join(target, name)
            d = os.path.join(link, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
            copied += 1
    return copied


def saves_location(user_dir):
    link = os.path.join(user_dir, "save games")
    if is_link(link):
        return os.path.realpath(link), True
    return link, False


def backup_saves(saves_dir, dest_zip, progress=None):
    """Zip every file in the saves folder. Returns number of files."""
    files = []
    for dirpath, _, filenames in os.walk(saves_dir):
        for fn in filenames:
            files.append(os.path.join(dirpath, fn))
    with zipfile.ZipFile(dest_zip + ".part", "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        for i, fp in enumerate(files, 1):
            z.write(fp, os.path.relpath(fp, saves_dir))
            if progress:
                progress(i, len(files))
    os.replace(dest_zip + ".part", dest_zip)
    return len(files)


def open_in_explorer(p):
    if not p or not os.path.exists(p):
        return False
    if os.name == "nt":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])
    return True


# ============================================================================ self-update (GitHub releases)

EXE_ASSET = "ParadoxModManager.exe"
PY_ASSET = "paradox_mod_manager.py"


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": f"ParadoxModManager/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release(repo):
    with http_get(f"https://api.github.com/repos/{repo}/releases/latest") as r:
        return json.loads(r.read().decode("utf-8"))


def release_asset(rel, name):
    for a in rel.get("assets", []) or []:
        if a.get("name") == name:
            return a
    return None


def download(url, dest, progress=None, timeout=30):
    h = hashlib.sha256()
    with http_get(url, timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


def expected_sha256(rel, asset_name):
    a = release_asset(rel, asset_name + ".sha256")
    if not a:
        return ""
    try:
        with http_get(a["browser_download_url"]) as r:
            m = re.search(r"[0-9a-fA-F]{64}", r.read().decode("utf-8", "replace"))
            return m.group(0).lower() if m else ""
    except OSError:
        return ""


def running_target():
    """(kind, path, asset): what to replace when updating."""
    if getattr(sys, "frozen", False):
        return "exe", sys.executable, EXE_ASSET
    return "py", os.path.abspath(__file__), PY_ASSET


def fetch_update(rel, progress=None):
    """Download the right asset next to the running file. Returns path of the downloaded file."""
    kind, target, asset_name = running_target()
    a = release_asset(rel, asset_name)
    if not a:
        raise OSError(f"The release has no {asset_name} to download.")
    new = target + ".new"
    digest = download(a["browser_download_url"], new, progress)
    size = int(a.get("size") or 0)
    if size and os.path.getsize(new) != size:
        os.remove(new)
        raise OSError("Download was incomplete — try again.")
    want = expected_sha256(rel, asset_name)
    if want and want != digest:
        os.remove(new)
        raise OSError("Downloaded file failed its checksum — not installed.")
    return new


def clean_env():
    env = {k: v for k, v in os.environ.items() if not (k.startswith("_MEI") or k.startswith("_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def install_update_and_restart(new_file):
    """Swap in the downloaded file once this process has exited, then start it again."""
    kind, target, _ = running_target()
    if kind == "py":
        shutil.copy2(target, target + ".old")
        os.replace(new_file, target)
        subprocess.Popen([sys.executable, target] + sys.argv[1:], cwd=os.path.dirname(target))
        return
    if os.name != "nt":
        os.replace(new_file, target)
        os.chmod(target, 0o755)
        subprocess.Popen([target], env=clean_env())
        return
    bat = os.path.join(tempfile.gettempdir(), f"pmm_update_{os.getpid()}.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f"""@echo off
set n=0
:retry
ping -n 2 127.0.0.1 >nul
move /y "{new_file}" "{target}" >nul 2>&1
if not errorlevel 1 goto done
set /a n+=1
if %n% lss 60 goto retry
goto end
:done
start "" "{target}"
:end
del "%~f0"
""")
    flags = 0x08000000 | 0x00000008 | 0x00000200  # no window, detached, new process group
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True, env=clean_env(),
                     cwd=os.path.dirname(target))


# ============================================================================ GUI

LIGHT = dict(bg="#f3f3f3", fg="#1d1d1d", field="#ffffff", sel="#2f6fb5", selfg="#ffffff", muted="#666666",
             enabled_bg="#eaf2ff", border="#c8c8c8", head="#e4e4e4", button="#e9e9e9", accent="#2f6fb5",
             launch="#1f7a3a", ok="#1f7a3a", minor="#9a6a00", bad="#b3261e", unknown="#666666",
             missing="#999999", banner="#fff4ce", banner_fg="#4a3b00")
DARK = dict(bg="#1f2125", fg="#e6e6e6", field="#2a2d33", sel="#3d6fb0", selfg="#ffffff", muted="#9aa0a6",
            enabled_bg="#26354b", border="#3a3e45", head="#2e3238", button="#33373e", accent="#3d7fd6",
            launch="#2e8b4a", ok="#62cc80", minor="#e0b44c", bad="#ff7b72", unknown="#9aa0a6",
            missing="#7d8187", banner="#3a3320", banner_fg="#f2dc9b")

MOD_COLS = ("on", "order", "name", "version", "supported", "compat", "conflicts", "source", "notes", "path")
MOD_HEADS = {"on": "", "order": "#", "name": "Mod", "version": "Mod version", "supported": "For game version",
             "compat": "Compatibility", "conflicts": "File conflicts", "source": "Source", "notes": "Notes",
             "path": "Location"}
MOD_WIDTHS = {"on": 30, "order": 40, "name": 270, "version": 95, "supported": 130, "compat": 150,
              "conflicts": 120, "source": 75, "notes": 200, "path": 260}


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.mods, self.enabled, self.dirty = {}, [], False
        self.dlcs, self.disabled_dlcs = [], set()
        self.game_raw, self.game_disp, self.note = "", "", ""
        self.backend = make_backend(self.key)
        self.sort_col, self.sort_rev = "name", False
        self.conf_cache, self.conf_summary, self.conf_files = {}, {}, {}
        self._conf_job, self._drag, self._busy = None, None, False
        self.release = None
        root.title(APP_NAME)
        root.minsize(1000, 700)
        geo = self.cfg["ui"].get("geometry")
        root.geometry(geo if geo else "1320x800")
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.dark_var = tk.BooleanVar(value=bool(self.cfg["ui"].get("dark")))
        self.auto_update_var = tk.BooleanVar(value=bool(self.cfg["ui"].get("auto_update", True)))
        self._build_menu()
        self._build()
        self.apply_theme()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.game_combo.set(GAMES[self.key]["name"])
        self.refresh(first=True)
        try:
            self.nb.select(int(self.cfg["ui"].get("last_tab", 0)))
        except (tk.TclError, ValueError):
            pass
        if self.auto_update_var.get():
            root.after(1500, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------------ helpers
    @property
    def key(self):
        return self.cfg["current_game"]

    @property
    def g(self):
        return self.cfg["games"][self.key]

    @property
    def pal(self):
        return DARK if self.dark_var.get() else LIGHT

    def user_dir(self):
        return self.g["user_dir"] or default_user_dir(self.key, self.g["install_dir"])

    def exe(self):
        return self.g["exe"] or detect_exe(self.key, self.g["install_dir"])

    def state(self):
        return {"mods": self.mods, "enabled": self.enabled, "dlcs": self.dlcs, "disabled": self.disabled_dlcs}

    def set_dirty(self, v=True):
        self.dirty = v
        self.root.title(("* " if v else "") + f"{APP_NAME} {__version__}")
        self.save_btn.configure(style="Accent.TButton" if v else "TButton")

    def status(self, text):
        self.status_var.set(text)

    def save_cfg(self):
        try:
            save_config(self.cfg)
        except OSError as e:
            self.status(f"Could not save settings: {e}")

    # ------------------------------------------------------------------ theme
    def apply_theme(self):
        P = self.pal
        s = self.style
        s.configure(".", background=P["bg"], foreground=P["fg"], fieldbackground=P["field"],
                    bordercolor=P["border"], lightcolor=P["bg"], darkcolor=P["border"], troughcolor=P["head"],
                    selectbackground=P["sel"], selectforeground=P["selfg"], insertcolor=P["fg"])
        s.configure("TFrame", background=P["bg"])
        s.configure("TLabel", background=P["bg"], foreground=P["fg"])
        s.configure("Muted.TLabel", background=P["bg"], foreground=P["muted"])
        s.configure("Header.TLabel", font=("TkDefaultFont", 11, "bold"))
        s.configure("Banner.TFrame", background=P["banner"])
        s.configure("Banner.TLabel", background=P["banner"], foreground=P["banner_fg"])
        s.configure("TButton", background=P["button"], foreground=P["fg"], bordercolor=P["border"])
        s.map("TButton", background=[("active", P["head"]), ("disabled", P["bg"])],
              foreground=[("disabled", P["muted"])])
        s.configure("Accent.TButton", foreground="white", background=P["accent"])
        s.map("Accent.TButton", background=[("active", P["sel"])])
        s.configure("Launch.TButton", foreground="white", background=P["launch"], padding=8)
        s.map("Launch.TButton", background=[("active", P["ok"])])
        s.configure("TCheckbutton", background=P["bg"], foreground=P["fg"])
        s.map("TCheckbutton", background=[("active", P["bg"])])
        s.configure("TLabelframe", background=P["bg"], bordercolor=P["border"])
        s.configure("TLabelframe.Label", background=P["bg"], foreground=P["fg"])
        s.configure("TEntry", fieldbackground=P["field"], foreground=P["fg"])
        s.configure("TCombobox", fieldbackground=P["field"], foreground=P["fg"], background=P["button"],
                    arrowcolor=P["fg"])
        s.map("TCombobox", fieldbackground=[("readonly", P["field"])], foreground=[("readonly", P["fg"])],
              selectbackground=[("readonly", P["field"])], selectforeground=[("readonly", P["fg"])])
        s.configure("TNotebook", background=P["bg"], bordercolor=P["border"])
        s.configure("TNotebook.Tab", background=P["head"], foreground=P["fg"], padding=(12, 4))
        s.map("TNotebook.Tab", background=[("selected", P["bg"])])
        s.configure("Treeview", background=P["field"], fieldbackground=P["field"], foreground=P["fg"],
                    rowheight=24, bordercolor=P["border"])
        s.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", P["selfg"])])
        s.configure("Treeview.Heading", background=P["head"], foreground=P["fg"], relief="flat")
        s.map("Treeview.Heading", background=[("active", P["border"])])
        s.configure("Vertical.TScrollbar", background=P["button"], arrowcolor=P["fg"])
        s.configure("Horizontal.TScrollbar", background=P["button"], arrowcolor=P["fg"])
        self.root.configure(background=P["bg"])
        self.root.option_add("*TCombobox*Listbox.background", P["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", P["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", P["sel"])
        for m in self._menus:
            m.configure(background=P["field"], foreground=P["fg"], activebackground=P["sel"],
                        activeforeground=P["selfg"], selectcolor=P["fg"])
        for t in (self.tree, self.dtree):
            for k in ("ok", "minor", "unknown", "missing"):
                t.tag_configure(k, foreground=P[k])
            t.tag_configure("old", foreground=P["bad"])
            t.tag_configure("new", foreground=P["bad"])
            t.tag_configure("enabled", background=P["enabled_bg"])
            t.tag_configure("on", foreground=P["ok"])
            t.tag_configure("off", foreground=P["missing"])

    def style_tk(self, w):
        """Colour a classic tk widget (Listbox / Text) for the current theme."""
        P = self.pal
        for k, v in (("background", P["field"]), ("foreground", P["fg"]), ("selectbackground", P["sel"]),
                     ("selectforeground", P["selfg"]), ("highlightbackground", P["border"]),
                     ("highlightcolor", P["sel"]), ("insertbackground", P["fg"])):
            try:
                w.configure(**{k: v})
            except tk.TclError:
                pass

    def toggle_dark(self):
        self.cfg["ui"]["dark"] = bool(self.dark_var.get())
        self.save_cfg()
        self.apply_theme()

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        self._menus = []
        mb = tk.Menu(self.root)
        self._menus.append(mb)

        def menu(label):
            m = tk.Menu(mb, tearoff=False)
            self._menus.append(m)
            mb.add_cascade(label=label, menu=m)
            return m

        f = menu("File")
        f.add_command(label="Save mods + DLC", accelerator="Ctrl+S", command=self.save_order)
        f.add_command(label="Undo last save", command=self.undo_last_save)
        f.add_separator()
        f.add_command(label="Export load order…", command=self.export_order)
        f.add_command(label="Import load order…", command=self.import_order)
        f.add_command(label="Copy load order as text", command=self.copy_order_text)
        f.add_separator()
        f.add_command(label="Back up save games…", command=self.backup_saves_ui)
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        t = menu("Tools")
        t.add_command(label="Sort by dependencies", command=self.sort_deps)
        t.add_command(label="Show file conflicts…", command=lambda: self.show_conflicts())
        t.add_separator()
        t.add_command(label="Open game folder", command=lambda: self._open(self.g["install_dir"]))
        t.add_command(label="Open user data folder", command=lambda: self._open(self.user_dir()))
        t.add_command(label="Open save games folder", command=lambda: self._open(saves_location(self.user_dir())[0]))
        t.add_separator()
        t.add_command(label="Settings / Locations…", command=self.open_settings)
        v = menu("View")
        v.add_checkbutton(label="Dark mode", variable=self.dark_var, command=self.toggle_dark)
        v.add_command(label="Refresh", accelerator="F5", command=self.refresh)
        h = menu("Help")
        h.add_command(label="Check for updates…", command=lambda: self.check_updates(manual=True))
        h.add_checkbutton(label="Check for updates on start", variable=self.auto_update_var,
                          command=self._toggle_auto_update)
        h.add_separator()
        h.add_command(label="Project page (GitHub)", command=lambda: self._web(f"https://github.com/{self.repo()}"))
        h.add_command(label="About", command=self.about)
        self.root.configure(menu=mb)
        self.root.bind("<Control-s>", lambda e: (self.save_order(), "break")[1])
        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Control-f>", lambda e: (self.filter_entry.focus_set(), "break")[1])

    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Game:").pack(side="left")
        self.game_combo = ttk.Combobox(top, state="readonly", width=26, values=[v["name"] for v in GAMES.values()])
        self.game_combo.pack(side="left", padx=6)
        self.game_combo.bind("<<ComboboxSelected>>", self.on_game_change)
        ttk.Button(top, text="Settings / Locations…", command=self.open_settings).pack(side="left", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=4)
        ttk.Label(top, text="Filter:").pack(side="left", padx=(20, 4))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: (self.populate(), self.populate_dlc()))
        self.filter_entry = ttk.Entry(top, textvariable=self.filter_var, width=28)
        self.filter_entry.pack(side="left")

        info = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        info.pack(fill="x")
        self.ver_var, self.paths_var = tk.StringVar(), tk.StringVar()
        ttk.Label(info, textvariable=self.ver_var, style="Header.TLabel").pack(anchor="w")
        ttk.Label(info, textvariable=self.paths_var, style="Muted.TLabel").pack(anchor="w")

        # update banner (shown only when a new version exists)
        self.banner = ttk.Frame(self.root, style="Banner.TFrame", padding=(10, 6))
        self.banner_var = tk.StringVar()
        ttk.Label(self.banner, textvariable=self.banner_var, style="Banner.TLabel").pack(side="left")
        ttk.Button(self.banner, text="✕", width=3, command=lambda: self.banner.pack_forget()).pack(side="right")
        ttk.Button(self.banner, text="Skip this version", command=self.skip_version).pack(side="right", padx=4)
        ttk.Button(self.banner, text="What's new", command=self.show_release_notes).pack(side="right", padx=4)
        self.update_btn = ttk.Button(self.banner, text="Update now", style="Accent.TButton",
                                     command=self.do_update)
        self.update_btn.pack(side="right", padx=4)

        self.status_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(8, 3),
                  relief="sunken").pack(fill="x", side="bottom")

        self.body = body = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")

        self.nb = ttk.Notebook(body)
        self.nb.pack(side="left", fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._remember_tab())

        # --- Mods tab
        tf = ttk.Frame(self.nb, padding=(0, 4, 0, 0))
        self.nb.add(tf, text="Mods")
        self.tree = ttk.Treeview(tf, columns=MOD_COLS, show="headings", selectmode="extended")
        for c in MOD_COLS:
            self.tree.heading(c, text=MOD_HEADS[c], command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=MOD_WIDTHS[c], stretch=c in ("name", "path", "notes"),
                             anchor="center" if c in ("on", "order") else "w")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.toggle() if self.tree.identify_region(e.x, e.y) == "cell" else None)
        self.tree.bind("<space>", lambda e: (self.toggle(), "break")[1])
        self.tree.bind("<Delete>", lambda e: self.set_selected(False))
        self.tree.bind("<Control-Up>", lambda e: (self.move(-1), "break")[1])
        self.tree.bind("<Control-Down>", lambda e: (self.move(1), "break")[1])
        self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._drag_end, add="+")
        self.tree.bind("<Button-3>", self._context_menu)
        self.tree.bind("<Button-2>", self._context_menu)

        # --- DLC tab
        df = ttk.Frame(self.nb, padding=(0, 4, 0, 0))
        self.nb.add(df, text="DLC")
        dbar = ttk.Frame(df)
        dbar.pack(fill="x", pady=(0, 4))
        ttk.Button(dbar, text="Enable all DLC", command=lambda: self.set_all_dlc(True)).pack(side="left")
        ttk.Button(dbar, text="Disable all DLC", command=lambda: self.set_all_dlc(False)).pack(side="left", padx=4)
        self.dlc_hint = tk.StringVar(value="Double-click / Space to toggle. Only switches off DLC you own.")
        ttk.Label(dbar, textvariable=self.dlc_hint, style="Muted.TLabel").pack(side="left", padx=10)
        dt = ttk.Frame(df)
        dt.pack(fill="both", expand=True)
        dcols = ("on", "name", "steam", "file")
        self.dtree = ttk.Treeview(dt, columns=dcols, show="headings", selectmode="extended")
        for c, h, w, st in (("on", "", 30, False), ("name", "DLC", 340, True),
                            ("steam", "Steam ID", 100, False), ("file", "ID used by the game", 360, True)):
            self.dtree.heading(c, text=h)
            self.dtree.column(c, width=w, stretch=st, anchor="center" if c == "on" else "w")
        dys = ttk.Scrollbar(dt, orient="vertical", command=self.dtree.yview)
        self.dtree.configure(yscrollcommand=dys.set)
        self.dtree.grid(row=0, column=0, sticky="nsew")
        dys.grid(row=0, column=1, sticky="ns")
        dt.rowconfigure(0, weight=1)
        dt.columnconfigure(0, weight=1)
        self.dtree.bind("<Double-1>", lambda e: self.toggle_dlc())
        self.dtree.bind("<space>", lambda e: (self.toggle_dlc(), "break")[1])

        # --- side panel
        def btn(text, cmd, **kw):
            b = ttk.Button(side, text=text, command=cmd, **kw)
            b.pack(fill="x", pady=2)
            return b

        launch = ttk.Button(side, text="▶  Launch game", command=self.launch, style="Launch.TButton")
        launch.pack(fill="x", side="bottom", pady=(2, 0))
        self.save_btn = ttk.Button(side, text="Save mods + DLC", command=self.save_order)
        self.save_btn.pack(fill="x", side="bottom", pady=2)

        def pair(a, b):
            fr = ttk.Frame(side)
            fr.pack(fill="x", pady=2)
            fr.columnconfigure((0, 1), weight=1, uniform="pair")
            ttk.Button(fr, text=a[0], command=a[1]).grid(row=0, column=0, sticky="ew", padx=(0, 2))
            ttk.Button(fr, text=b[0], command=b[1]).grid(row=0, column=1, sticky="ew", padx=(2, 0))

        btn("Enable / Disable  (Space)", self.toggle)
        pair(("▲ Up", lambda: self.move(-1)), ("▼ Down", lambda: self.move(1)))
        pair(("⤒ Top", lambda: self.move_end(True)), ("⤓ Bottom", lambda: self.move_end(False)))
        pair(("Enable all shown", lambda: self.set_all(True)), ("Disable all", lambda: self.set_all(False)))
        ttk.Label(side, text="Tip: drag enabled mods to reorder, right-click for more.",
                  style="Muted.TLabel", wraplength=230).pack(anchor="w", pady=(2, 0))
        ttk.Separator(side).pack(fill="x", pady=6)
        btn("Sort by dependencies", self.sort_deps)
        btn("Show file conflicts…", lambda: self.show_conflicts())
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Label(side, text="Playsets").pack(anchor="w")
        self.playset_combo = ttk.Combobox(side, state="readonly", width=24)
        self.playset_combo.pack(fill="x", pady=2)
        pf = ttk.Frame(side)
        pf.pack(fill="x")
        ttk.Button(pf, text="Load", command=self.playset_load).pack(side="left", expand=True, fill="x")
        ttk.Button(pf, text="Save as…", command=self.playset_save).pack(side="left", expand=True, fill="x")
        ttk.Button(pf, text="Delete", command=self.playset_delete).pack(side="left", expand=True, fill="x")
        ttk.Separator(side).pack(fill="x", pady=6)
        btn("Add mod library folder…", self.add_library)
        btn("Back up save games…", self.backup_saves_ui)

        self.ctx = tk.Menu(self.root, tearoff=False)
        self._menus.append(self.ctx)

    # ------------------------------------------------------------------ loading
    def on_game_change(self, _=None):
        name = self.game_combo.get()
        new = next(k for k, v in GAMES.items() if v["name"] == name)
        if new == self.key:
            return
        if self.dirty and not self.confirm_discard():
            self.game_combo.set(GAMES[self.key]["name"])
            return
        self.dirty = False
        self.cfg["current_game"] = new
        self.save_cfg()
        self.refresh()

    def confirm_discard(self):
        r = messagebox.askyesnocancel(APP_NAME, "Save your mod and DLC changes first?", parent=self.root)
        if r is None:
            return False
        if r:
            return self.save_order()
        return True

    def refresh(self, first=False):
        if self.dirty and not first and not self.confirm_discard():
            return
        g = self.g
        self.backend = make_backend(self.key)
        ud = self.user_dir()
        self.game_raw, self.game_disp = detect_game_version(g["install_dir"], ud)
        if g["version_override"]:
            self.game_raw = self.game_disp = g["version_override"]
        try:
            st = self.backend.load(g, ud)
        except Exception as e:  # never die on a malformed file
            st = {"mods": {}, "enabled": [], "dlcs": [], "disabled": set(), "errors": [str(e)], "note": ""}
        self.mods, self.enabled = st["mods"], st["enabled"]
        self.dlcs, self.disabled_dlcs = st["dlcs"], set(st["disabled"])
        self.note = st.get("note", "")
        self.conf_cache, self.conf_summary, self.conf_files = {}, {}, {}
        self.set_dirty(False)
        self.update_header()
        self.update_playsets()
        self.populate()
        self.populate_dlc()
        fmt = GAMES[self.key]["format"]
        self.dlc_hint.set("EU5: lists the DLC in the game's active playset plus any newly installed. "
                          "Only switches off DLC you own." if fmt == "playsets" else
                          "Double-click / Space to toggle. Only switches off DLC you own.")
        game = GAMES[self.key]["name"]
        if not g["install_dir"]:
            self.status(f"Set the {game} install folder in Settings / Locations to show the game version and launch.")
            if first:
                self.root.after(300, self.open_settings)
        elif not os.path.isdir(ud):
            self.status(f"User data folder not found: {ud} — set it in Settings / Locations.")
        else:
            n = len([m for m in self.mods.values() if m["source"] != "Missing"])
            msg = f"{n} mods found, {len(self.enabled)} enabled.  {self.dlc_summary()}"
            if st["errors"]:
                msg += f"  {len(st['errors'])} problem(s): {st['errors'][0]}"
            self.status(msg)
        self.schedule_conflicts()

    def update_header(self):
        v = self.game_disp or "unknown (set install folder or a version override)"
        self.ver_var.set(f"{GAMES[self.key]['name']} — game version {v}" + (f"    ·  {self.note}" if self.note else ""))
        loc, linked = saves_location(self.user_dir())
        self.paths_var.set(f"Install:    {self.g['install_dir'] or '—'}\n"
                           f"User data:  {self.user_dir()}\n"
                           f"Saves:      {loc}{'   (redirected)' if linked else ''}")

    # ------------------------------------------------------------------ mod list
    def notes_for(self, m, deps):
        if m["source"] == "Missing":
            return "Not installed — disable to remove"
        if not m["exists"]:
            return "Mod files not found"
        return "; ".join(deps.get(m["id"], []))

    def conflict_text(self, mid):
        if mid not in self.enabled:
            return ""
        s = self.conf_summary.get(mid)
        if s is None:
            return "…" if self.mods.get(mid, {}).get("exists") else ""
        parts = []
        if s["wins"]:
            parts.append(f"wins {s['wins']}")
        if s["loses"]:
            parts.append(f"loses {s['loses']}")
        return " · ".join(parts) or "none"

    def sort_key(self, mid):
        m = self.mods[mid]
        c = self.sort_col
        if c in ("name", "version", "supported", "source", "path"):
            return (m.get(c) or "").lower()
        if c == "compat":
            return compat_status(m["supported"], self.game_raw)[1]
        return m["name"].lower()

    def sort_by(self, col):
        if col in ("on", "order"):
            self.sort_col, self.sort_rev = "name", False
        elif self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, False
        for c in MOD_COLS:
            arrow = (" ▼" if self.sort_rev else " ▲") if c == self.sort_col and c not in ("on", "order") else ""
            self.tree.heading(c, text=MOD_HEADS[c] + arrow)
        self.populate()
        self.status("Enabled mods stay in load order; the sort applies to the mods that are switched off.")

    def populate(self):
        sel = set(self.tree.selection())
        yv = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        flt = self.filter_var.get().strip().lower()
        rest = sorted((k for k in self.mods if k not in self.enabled), key=self.sort_key, reverse=self.sort_rev)
        deps = dependency_problems(self.mods, self.enabled)
        for mid in list(self.enabled) + rest:
            m = self.mods[mid]
            if flt and flt not in m["name"].lower() and flt not in " ".join(m["tags"]).lower() \
                    and flt not in (m["remote_id"] or ""):
                continue
            on = mid in self.enabled
            if m["source"] == "Missing" or not m["exists"]:
                kind, label = "missing", "— missing"
            else:
                kind, label = compat_status(m["supported"], self.game_raw)
            self.tree.insert("", "end", iid=mid, tags=[kind] + (["enabled"] if on else []), values=(
                "☑" if on else "☐", self.enabled.index(mid) + 1 if on else "", m["name"],
                m["version"] or "—", m["supported"] or "—", label, self.conflict_text(mid), m["source"],
                self.notes_for(m, deps), m["path"]))
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self.tree.yview_moveto(yv[0])
        self.nb.tab(0, text=f"Mods ({len(self.enabled)} on)")

    def selected(self):
        return list(self.tree.selection())

    def _changed(self, rescan=False):
        self.set_dirty()
        self.populate()
        if rescan:
            self.schedule_conflicts()
        else:
            self._compute_conflicts()

    def toggle(self):
        if self.on_dlc_tab():
            return self.toggle_dlc()
        sel = self.selected()
        if not sel:
            return
        self.set_selected(any(s not in self.enabled for s in sel), sel)

    def set_selected(self, on, sel=None):
        sel = sel or self.selected()
        if not sel:
            return
        for s in sel:
            if on and s not in self.enabled and self.mods[s]["source"] != "Missing":
                self.enabled.append(s)
            elif not on and s in self.enabled:
                self.enabled.remove(s)
        for mid in [k for k, m in self.mods.items() if m["source"] == "Missing" and k not in self.enabled]:
            del self.mods[mid]
        self._changed(rescan=True)

    def move(self, d):
        sel = [s for s in self.selected() if s in self.enabled]
        if not sel:
            return
        idx = sorted(self.enabled.index(s) for s in sel)
        if d > 0:
            idx.reverse()
        for i in idx:
            j = i + d
            if 0 <= j < len(self.enabled) and self.enabled[j] not in sel:
                self.enabled[i], self.enabled[j] = self.enabled[j], self.enabled[i]
        self._changed()
        self.tree.see(sel[0])

    def move_end(self, top, sel=None):
        sel = [s for s in self.enabled if s in (sel or self.selected())]
        if not sel:
            return
        rest = [s for s in self.enabled if s not in sel]
        self.enabled = sel + rest if top else rest + sel
        self._changed()

    def set_all(self, on):
        if self.on_dlc_tab():
            return self.set_all_dlc(on)
        if on:
            for iid in self.tree.get_children():
                if iid not in self.enabled and self.mods[iid]["exists"]:
                    self.enabled.append(iid)
        else:
            self.enabled = []
            for mid in [k for k, m in self.mods.items() if m["source"] == "Missing"]:
                del self.mods[mid]
        self._changed(rescan=True)

    def sort_deps(self):
        new = sort_by_dependencies(self.mods, self.enabled)
        if new == self.enabled:
            missing = sum(1 for v in dependency_problems(self.mods, self.enabled).values()
                          for p in v if p.startswith("needs"))
            self.status("Load order already satisfies every dependency."
                        + (f"  {missing} required mod(s) are not enabled/installed — see Notes." if missing else ""))
            return
        self.enabled = new
        self._changed()
        self.status("Reordered so every dependency loads before the mods that need it. Save to apply.")

    # drag and drop ----------------------------------------------------
    def _drag_start(self, e):
        row = self.tree.identify_row(e.y)
        self._drag = row if row in self.enabled and self.tree.identify_region(e.x, e.y) == "cell" else None
        self._drag_moved = False

    def _drag_motion(self, e):
        if not self._drag:
            return
        target = self.tree.identify_row(e.y)
        if not target or target == self._drag or target not in self.enabled:
            return
        i, j = self.enabled.index(self._drag), self.enabled.index(target)
        self.enabled.insert(j, self.enabled.pop(i))
        self._drag_moved = True
        self.set_dirty()
        self.populate()
        self.tree.selection_set(self._drag)
        self.tree.configure(cursor="fleur")

    def _drag_end(self, e):
        if self._drag and self._drag_moved:
            self._compute_conflicts()
            self.status("Moved. Drag rows to reorder the load order; Save to apply.")
        self._drag = None
        self.tree.configure(cursor="")

    # context menu -----------------------------------------------------
    def _context_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        sel = self.selected()
        m = self.mods[row]
        c = self.ctx
        c.delete(0, "end")
        any_off = any(s not in self.enabled for s in sel)
        c.add_command(label="Enable" if any_off else "Disable", command=lambda: self.set_selected(any_off, sel))
        if any(s in self.enabled for s in sel):
            c.add_command(label="Move to top", command=lambda: self.move_end(True, sel))
            c.add_command(label="Move to bottom", command=lambda: self.move_end(False, sel))
        c.add_separator()
        if row in self.enabled:
            c.add_command(label="Show file conflicts for this mod…", command=lambda: self.show_conflicts(row))
        c.add_command(label="Open mod folder", command=self.open_selected,
                      state="normal" if m["exists"] else "disabled")
        c.add_command(label="Open Steam Workshop page", state="normal" if m["remote_id"] else "disabled",
                      command=lambda: self._web(f"https://steamcommunity.com/sharedfiles/filedetails/?id={m['remote_id']}"))
        c.add_command(label="Copy name", command=lambda: self._copy(m["name"]))
        c.add_command(label="Copy path", command=lambda: self._copy(m["path"]))
        try:
            c.tk_popup(e.x_root, e.y_root)
        finally:
            c.grab_release()

    # ------------------------------------------------------------------ file conflicts
    def schedule_conflicts(self):
        if self._conf_job:
            self.root.after_cancel(self._conf_job)
        self._conf_job = self.root.after(500, self._start_conflict_scan)

    def _start_conflict_scan(self):
        self._conf_job = None
        todo = [(mid, self.mods[mid]["path"]) for mid in self.enabled
                if mid in self.mods and self.mods[mid]["exists"] and mid not in self.conf_cache]
        if not todo:
            self._compute_conflicts()
            return
        cache, gen = self.conf_cache, object()
        self._conf_gen = gen

        def work():
            for mid, path in todo:
                try:
                    cache[mid] = mod_files(path)
                except OSError:
                    cache[mid] = set()
            self.root.after(0, lambda: self._compute_conflicts() if self._conf_gen is gen else None)

        threading.Thread(target=work, daemon=True).start()

    def _compute_conflicts(self):
        owners = {}
        for mid in self.enabled:
            for rel in self.conf_cache.get(mid, ()):
                owners.setdefault(rel, []).append(mid)
        self.conf_files = {r: ids for r, ids in owners.items() if len(ids) > 1}
        self.conf_summary = {mid: {"wins": 0, "loses": 0} for mid in self.enabled if mid in self.conf_cache}
        for ids in self.conf_files.values():
            self.conf_summary[ids[-1]]["wins"] += 1
            for mid in ids[:-1]:
                self.conf_summary[mid]["loses"] += 1
        for mid in self.enabled:
            if self.tree.exists(mid):
                self.tree.set(mid, "conflicts", self.conflict_text(mid))

    def show_conflicts(self, only=None):
        if self.on_dlc_tab():
            self.nb.select(0)
        w = tk.Toplevel(self.root)
        w.title("File conflicts")
        w.geometry("980x560")
        w.configure(background=self.pal["bg"])
        bar = ttk.Frame(w, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Show:").pack(side="left")
        choices = ["All enabled mods"] + [self.mods[m]["name"] for m in self.enabled if m in self.conf_summary]
        combo = ttk.Combobox(bar, state="readonly", values=choices, width=50)
        combo.pack(side="left", padx=6)
        combo.set(self.mods[only]["name"] if only in self.mods and self.mods[only]["name"] in choices else choices[0])
        info = tk.StringVar()
        ttk.Label(bar, textvariable=info, style="Muted.TLabel").pack(side="left", padx=10)
        ttk.Label(w, padding=(8, 0), style="Muted.TLabel",
                  text="When two mods contain the same file, the one lower in the load order wins and the other's "
                       "copy is ignored. Move a mod down to make it win.").pack(anchor="w")
        f = ttk.Frame(w, padding=8)
        f.pack(fill="both", expand=True)
        t = ttk.Treeview(f, columns=("file", "winner", "losers"), show="headings")
        for c, h, wd in (("file", "File", 420), ("winner", "Used from (wins)", 220), ("losers", "Overridden in", 300)):
            t.heading(c, text=h)
            t.column(c, width=wd, stretch=True)
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        t.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

        def fill(*_):
            t.delete(*t.get_children())
            name = combo.get()
            target = next((m for m in self.enabled if self.mods[m]["name"] == name), None) \
                if name != choices[0] else None
            rows = 0
            for rel in sorted(self.conf_files):
                ids = self.conf_files[rel]
                if target and target not in ids:
                    continue
                t.insert("", "end", values=(rel, self.mods[ids[-1]]["name"],
                                            ", ".join(self.mods[i]["name"] for i in ids[:-1])))
                rows += 1
                if rows >= 20000:
                    break
            pending = any(m not in self.conf_cache for m in self.enabled if self.mods[m]["exists"])
            info.set(f"{rows} conflicting file(s)" + ("  — still scanning, reopen in a moment" if pending else ""))

        combo.bind("<<ComboboxSelected>>", fill)
        fill()

    # ------------------------------------------------------------------ DLC
    def on_dlc_tab(self):
        try:
            return self.nb.index(self.nb.select()) == 1
        except tk.TclError:
            return False

    def _remember_tab(self):
        try:
            self.cfg["ui"]["last_tab"] = self.nb.index(self.nb.select())
        except tk.TclError:
            pass

    def dlc_summary(self):
        if not self.dlcs:
            return "No DLC found (set the install folder)." if not self.g["install_dir"] else "No DLC listed."
        on = sum(1 for d in self.dlcs if d["id"] not in self.disabled_dlcs)
        return f"DLC: {on} of {len(self.dlcs)} enabled."

    def populate_dlc(self):
        sel = set(self.dtree.selection())
        self.dtree.delete(*self.dtree.get_children())
        flt = self.filter_var.get().strip().lower()
        for d in self.dlcs:
            if flt and flt not in d["name"].lower() and flt not in d["id"].lower():
                continue
            on = d["id"] not in self.disabled_dlcs
            if not self.dtree.exists(d["id"]):
                self.dtree.insert("", "end", iid=d["id"], tags=("on" if on else "off",),
                                  values=("☑" if on else "☐", d["name"], d["steam_id"] or "—", d["id"]))
        keep = [i for i in sel if self.dtree.exists(i)]
        if keep:
            self.dtree.selection_set(keep)
        on = sum(1 for d in self.dlcs if d["id"] not in self.disabled_dlcs)
        self.nb.tab(1, text=f"DLC ({on}/{len(self.dlcs)})" if self.dlcs else "DLC")

    def toggle_dlc(self):
        sel = list(self.dtree.selection())
        if not sel:
            return
        turn_on = any(s in self.disabled_dlcs for s in sel)
        for s in sel:
            (self.disabled_dlcs.discard if turn_on else self.disabled_dlcs.add)(s)
        self.set_dirty()
        self.populate_dlc()
        self.status(self.dlc_summary() + "  Save or Launch to apply.")

    def set_all_dlc(self, on):
        for i in self.dtree.get_children():
            (self.disabled_dlcs.discard if on else self.disabled_dlcs.add)(i)
        self.set_dirty()
        self.populate_dlc()
        self.status(self.dlc_summary() + "  Save or Launch to apply.")

    # ------------------------------------------------------------------ saving / undo / export
    def save_order(self):
        ud = self.user_dir()
        if not os.path.isdir(ud):
            messagebox.showerror(APP_NAME, f"User data folder not found:\n{ud}", parent=self.root)
            return False
        try:
            self.backend.save(ud, self.state())
        except (OSError, ValueError, KeyError) as e:
            messagebox.showerror(APP_NAME, f"Could not save {self.backend.load_file}:\n{e}", parent=self.root)
            return False
        self.set_dirty(False)
        self.status(f"Saved: {len(self.enabled)} mods enabled.  {self.dlc_summary()}")
        return True

    def undo_last_save(self):
        ud = self.user_dir()
        cur, bak = self.backend.load_path(ud), self.backend.backup_path(ud)
        if not os.path.isfile(bak):
            messagebox.showinfo(APP_NAME, "There is no earlier save to go back to yet.", parent=self.root)
            return
        if not messagebox.askyesno(APP_NAME, "Restore the load order and DLC settings from before your last save?\n"
                                             "(Doing this again switches back.)", parent=self.root):
            return
        try:
            tmp = cur + ".swap"
            if os.path.isfile(cur):
                shutil.copy2(cur, tmp)
            shutil.copy2(bak, cur)
            if os.path.isfile(tmp):
                os.replace(tmp, bak)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Undo failed:\n{e}", parent=self.root)
            return
        self.dirty = False
        self.refresh()
        self.status("Restored the previous load order.")

    def export_order(self):
        p = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                         initialfile=f"{self.key}-load-order.json",
                                         filetypes=[("Load order", "*.json"), ("All files", "*.*")])
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(export_load_order(self.key, self.mods, self.enabled, self.disabled_dlcs), f, indent=2)
            self.status(f"Exported {len(self.enabled)} mods to {p}")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Export failed:\n{e}", parent=self.root)

    def import_order(self):
        p = filedialog.askopenfilename(parent=self.root, filetypes=[("Load order", "*.json"), ("All files", "*.*")])
        if not p:
            return
        data = read_json(p)
        if not isinstance(data, dict) or "mods" not in data:
            messagebox.showerror(APP_NAME, "That file isn't a load order exported by this app.", parent=self.root)
            return
        if data.get("game") and data["game"] != self.key and not messagebox.askyesno(
                APP_NAME, f"This load order is for {GAMES.get(data['game'], {}).get('name', data['game'])}. "
                          "Import it anyway?", parent=self.root):
            return
        enabled, missing = import_load_order(data, self.mods)
        self.enabled = enabled
        if data.get("game") == self.key and isinstance(data.get("disabled_dlcs"), list):
            self.disabled_dlcs = set(data["disabled_dlcs"])
            self.populate_dlc()
        self._changed(rescan=True)
        msg = f"Imported {len(enabled)} mods. Save or Launch to apply."
        if missing:
            messagebox.showwarning(APP_NAME, f"{len(missing)} mod(s) from the file aren't installed:\n\n"
                                   + "\n".join(missing[:30]), parent=self.root)
        self.status(msg)

    def copy_order_text(self):
        lines = []
        for i, mid in enumerate(self.enabled, 1):
            m = self.mods[mid]
            extra = f"  (Workshop {m['remote_id']})" if m["remote_id"] else ""
            lines.append(f"{i}. {m['name']}" + (f" v{m['version']}" if m["version"] else "") + extra)
        self._copy(f"{GAMES[self.key]['name']} load order ({len(lines)} mods)\n" + "\n".join(lines))
        self.status("Load order copied to the clipboard.")

    # ------------------------------------------------------------------ playsets (app's own)
    def update_playsets(self):
        names = sorted(self.g["playsets"])
        self.playset_combo["values"] = names
        if self.playset_combo.get() not in names:
            self.playset_combo.set("")

    def playset_save(self):
        name = simpledialog.askstring(APP_NAME, "Playset name:", initialvalue=self.playset_combo.get(),
                                      parent=self.root)
        if not name:
            return
        self.g["playsets"][name] = {"mods": list(self.enabled), "disabled_dlcs": sorted(self.disabled_dlcs)}
        self.save_cfg()
        self.update_playsets()
        self.playset_combo.set(name)
        self.status(f"Playset '{name}' saved.")

    def playset_load(self):
        name = self.playset_combo.get()
        if not name:
            return
        ps = self.g["playsets"].get(name, [])
        wanted = ps.get("mods", []) if isinstance(ps, dict) else ps
        if isinstance(ps, dict):
            self.disabled_dlcs = set(ps.get("disabled_dlcs", []))
        self.enabled = [m for m in wanted if m in self.mods and self.mods[m]["source"] != "Missing"]
        missing = len(wanted) - len(self.enabled)
        self.populate_dlc()
        self._changed(rescan=True)
        self.status(f"Playset '{name}' loaded — Save or Launch to apply."
                    + (f"  {missing} mod(s) in it are no longer installed." if missing else ""))

    def playset_delete(self):
        name = self.playset_combo.get()
        if name and messagebox.askyesno(APP_NAME, f"Delete playset '{name}'?", parent=self.root):
            self.g["playsets"].pop(name, None)
            self.save_cfg()
            self.update_playsets()

    # ------------------------------------------------------------------ misc actions
    def add_library(self):
        d = filedialog.askdirectory(parent=self.root, title="Choose a folder that contains mods (or a single mod)")
        if d:
            if norm(d) not in map(norm, self.g["libraries"]):
                self.g["libraries"].append(d)
                self.save_cfg()
            self.refresh()

    def _open(self, p):
        if not open_in_explorer(p):
            messagebox.showinfo(APP_NAME, f"Folder does not exist yet:\n{p or '(not set)'}", parent=self.root)

    def _web(self, url):
        import webbrowser
        webbrowser.open(url)

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def open_selected(self):
        sel = self.selected()
        if sel:
            p = self.mods[sel[0]]["path"]
            self._open(p if os.path.isdir(p) else os.path.dirname(p))

    def backup_saves_ui(self):
        src, _ = saves_location(self.user_dir())
        if not os.path.isdir(src) or not os.listdir(src):
            messagebox.showinfo(APP_NAME, f"No save games found in:\n{src}", parent=self.root)
            return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        dest = filedialog.asksaveasfilename(
            parent=self.root, title="Save backup as", defaultextension=".zip",
            initialdir=self.g.get("backup_dir") or os.path.dirname(src),
            initialfile=f"{self.key}-saves-{stamp}.zip", filetypes=[("Zip archive", "*.zip")])
        if not dest:
            return
        self.g["backup_dir"] = os.path.dirname(dest)
        self.save_cfg()

        def work():
            try:
                n = backup_saves(src, dest, lambda i, t: self.root.after(
                    0, lambda: self.status(f"Backing up saves… {i}/{t}")))
                self.root.after(0, lambda: self.status(f"Backed up {n} file(s) to {dest}"))
            except (OSError, zipfile.BadZipFile) as e:
                self.root.after(0, lambda e=e: messagebox.showerror(APP_NAME, f"Backup failed:\n{e}",
                                                                    parent=self.root))

        self.status("Backing up saves…")
        threading.Thread(target=work, daemon=True).start()

    def launch(self):
        exe = self.exe()
        if not exe or not os.path.isfile(exe):
            messagebox.showerror(APP_NAME, "Game executable not found. Set it in Settings / Locations.",
                                 parent=self.root)
            return
        if self.g.get("pre_launch_check", True):
            probs = launch_problems(self.mods, self.enabled, self.game_raw)
            if probs and not self.prelaunch_dialog(probs):
                return
        if not self.save_order():
            return
        args = [exe] + [str(a) for a in launcher_settings(self.g["install_dir"]).get("exeArgs", []) or []]
        if self.g["launch_args"].strip():
            args += self.g["launch_args"].split()
        try:
            subprocess.Popen(args, cwd=os.path.dirname(exe))
            self.status(f"Launched {os.path.basename(exe)} with {len(self.enabled)} mods.")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not launch the game:\n{e}", parent=self.root)

    def prelaunch_dialog(self, probs):
        res = {"go": False}
        w = tk.Toplevel(self.root)
        w.title("Check before launching")
        w.transient(self.root)
        w.configure(background=self.pal["bg"])
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{len(probs)} possible problem(s) with the enabled mods:",
                  style="Header.TLabel").pack(anchor="w")
        txt = tk.Text(f, width=90, height=min(16, len(probs) + 2), wrap="word", relief="flat")
        self.style_tk(txt)
        txt.insert("1.0", "\n".join("• " + p for p in probs))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, pady=8)
        skip = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Don't check before launching", variable=skip).pack(anchor="w")
        bb = ttk.Frame(f)
        bb.pack(fill="x", pady=(10, 0))

        def go(fix=False):
            if fix:
                self.enabled = sort_by_dependencies(self.mods, self.enabled)
                self._changed()
            res["go"] = True
            close()

        def close():
            if skip.get():
                self.g["pre_launch_check"] = False
                self.save_cfg()
            w.destroy()

        ttk.Button(bb, text="Cancel", command=close).pack(side="right")
        ttk.Button(bb, text="Launch anyway", command=go).pack(side="right", padx=4)
        if any("load after" in p for p in probs):
            ttk.Button(bb, text="Fix order & launch", style="Accent.TButton",
                       command=lambda: go(True)).pack(side="right", padx=4)
        w.grab_set()
        self.root.wait_window(w)
        return res["go"]

    def open_settings(self):
        SettingsDialog(self)

    def on_close(self):
        if self.dirty and not self.confirm_discard():
            return
        self.cfg["ui"]["geometry"] = self.root.geometry()
        self._remember_tab()
        self.save_cfg()
        self.root.destroy()

    def about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME} {__version__}\n\nOffline mod & DLC manager for Paradox games.\n"
                                      f"Updates: github.com/{self.repo()}\nSettings file: {config_path()}",
                            parent=self.root)

    # ------------------------------------------------------------------ self-update
    def repo(self):
        return self.cfg["ui"].get("update_repo") or UPDATE_REPO

    def _toggle_auto_update(self):
        self.cfg["ui"]["auto_update"] = bool(self.auto_update_var.get())
        self.save_cfg()

    def check_updates(self, manual=False):
        if manual:
            self.status("Checking for updates…")

        def work():
            try:
                rel = latest_release(self.repo())
                err = None
            except Exception as e:  # network errors, 404 when no release yet, etc.
                rel, err = None, e
            self.root.after(0, lambda: self._update_result(rel, err, manual))

        threading.Thread(target=work, daemon=True).start()

    def _update_result(self, rel, err, manual):
        if err is not None or not isinstance(rel, dict) or not rel.get("tag_name"):
            if manual:
                self.status("")
                messagebox.showwarning(APP_NAME, f"Couldn't check for updates:\n{err or 'no releases found'}",
                                       parent=self.root)
            return
        tag = rel["tag_name"]
        if not is_newer(tag, __version__):
            if manual:
                self.status("")
                messagebox.showinfo(APP_NAME, f"You have the latest version ({__version__}).", parent=self.root)
            return
        if not manual and tag == self.cfg["ui"].get("skip_version"):
            return
        self.release = rel
        self.banner_var.set(f"Update available: version {tag.lstrip('v')} (you have {__version__}).")
        self.banner.pack(fill="x", before=self.body)
        self.update_btn.configure(state="normal")
        if manual:
            self.status("")
            if messagebox.askyesno(APP_NAME, f"Version {tag.lstrip('v')} is available (you have {__version__}).\n\n"
                                             "Update now? The app restarts itself.", parent=self.root):
                self.do_update()

    def skip_version(self):
        if self.release:
            self.cfg["ui"]["skip_version"] = self.release["tag_name"]
            self.save_cfg()
        self.banner.pack_forget()

    def show_release_notes(self):
        rel = self.release or {}
        w = tk.Toplevel(self.root)
        w.title(f"What's new in {rel.get('tag_name', '')}")
        w.configure(background=self.pal["bg"])
        txt = tk.Text(w, width=90, height=24, wrap="word", relief="flat", padx=10, pady=10)
        self.style_tk(txt)
        txt.insert("1.0", rel.get("body") or "No release notes.")
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

    def do_update(self):
        rel = self.release
        if not rel or self._busy:
            return
        if self.dirty and not self.confirm_discard():
            return
        self._busy = True
        self.update_btn.configure(state="disabled")
        self.status("Downloading update…")

        def prog(done, total):
            self.root.after(0, lambda: self.status(
                f"Downloading update… {done * 100 // total}%" if total else f"Downloading update… {done // 1024} KB"))

        def work():
            try:
                new = fetch_update(rel, prog)
                self.root.after(0, lambda: self._finish_update(new))
            except Exception as e:
                def fail(e=e):
                    self._busy = False
                    self.update_btn.configure(state="normal")
                    self.status("")
                    messagebox.showerror(APP_NAME, f"Update failed:\n{e}", parent=self.root)
                self.root.after(0, fail)

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, new):
        self.cfg["ui"]["geometry"] = self.root.geometry()
        self._remember_tab()
        self.save_cfg()
        try:
            install_update_and_restart(new)
        except OSError as e:
            self._busy = False
            messagebox.showerror(APP_NAME, f"Update failed:\n{e}", parent=self.root)
            return
        self.root.destroy()


class SettingsDialog:
    FIELDS = ("install_dir", "exe", "user_dir", "version_override", "launch_args")

    def __init__(self, app):
        self.app = app
        g = app.g
        self.w = w = tk.Toplevel(app.root)
        w.title(f"Settings / Locations — {GAMES[app.key]['name']}")
        w.transient(app.root)
        w.configure(background=app.pal["bg"])
        self.v = {k: tk.StringVar(value=g.get(k, "")) for k in self.FIELDS}
        self.scan_ws = tk.BooleanVar(value=g.get("scan_workshop", True))
        self.precheck = tk.BooleanVar(value=g.get("pre_launch_check", True))
        outer = ttk.Frame(w, padding=10)
        outer.pack(fill="both", expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        nb.add(f, text="Locations")
        f2 = ttk.Frame(nb, padding=12)
        f2.columnconfigure(0, weight=1)
        nb.add(f2, text="Mod folders & saves")
        f3 = ttk.Frame(nb, padding=12)
        f3.columnconfigure(0, weight=1)
        nb.add(f3, text="Advanced")
        self.f, self.row, self.hints = f, 0, []
        meta = GAMES[app.key]["format"] != "classic"

        self.path_row("Game install folder", "install_dir", "dir", ("Detect (Steam)", self.detect))
        self.hint(f"The folder containing the game (e.g. …/steamapps/common/{GAMES[app.key]['steam_dir']}).")
        self.path_row("Game executable", "exe", "file")
        self.hint(lambda: "Auto: " + (detect_exe(app.key, self.v["install_dir"].get()) or "not found — browse to it"))
        self.path_row("User data folder", "user_dir", "dir", ("Use default", lambda: self.v["user_dir"].set("")))
        self.hint(lambda: "Default: " + default_user_dir(app.key, self.v["install_dir"].get()) +
                  f"   (holds mod/, {app.backend.load_file}, save games)")
        self.text_row("Game version override", "version_override")
        self.hint(lambda: "Detected: " + (detect_game_version(self.v["install_dir"].get(), self.cur_ud())[1]
                                          or "unknown") + "   — leave blank to use detected")
        self.text_row("Extra launch arguments", "launch_args")
        self.hint("e.g. -debug_mode")
        ttk.Checkbutton(f, text="Warn about problems (missing dependencies, outdated mods…) before launching",
                        variable=self.precheck).grid(row=self.row, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.row += 1

        lf = ttk.LabelFrame(f2, text="Mod library folders (mods kept anywhere, picked up automatically)", padding=8)
        lf.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        lf.columnconfigure(0, weight=1)
        self.libs = tk.Listbox(lf, height=4)
        app.style_tk(self.libs)
        self.libs.grid(row=0, column=0, rowspan=2, sticky="nsew")
        for l in g["libraries"]:
            self.libs.insert("end", l)
        ttk.Button(lf, text="Add…", command=self.lib_add).grid(row=0, column=1, padx=6, sticky="ew")
        ttk.Button(lf, text="Remove", command=self.lib_remove).grid(row=1, column=1, padx=6, sticky="ew")
        ttk.Checkbutton(lf, text="Also pick up Steam Workshop downloads directly (no Paradox launcher needed)",
                        variable=self.scan_ws).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        sf = ttk.LabelFrame(f2, text="Save games location", padding=8)
        sf.grid(row=1, column=0, sticky="ew", pady=4)
        sf.columnconfigure(1, weight=1)
        self.saves_info = tk.StringVar()
        ttk.Label(sf, textvariable=self.saves_info).grid(row=0, column=0, columnspan=4, sticky="w")
        self.saves_target = tk.StringVar(value=g.get("saves_dir", ""))
        ttk.Label(sf, text="New folder:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(sf, textvariable=self.saves_target).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(sf, text="Browse…", command=lambda: self.browse(self.saves_target, "dir")).grid(row=1, column=2)
        bf = ttk.Frame(sf)
        bf.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Button(bf, text="Redirect saves here", command=self.do_redirect).pack(side="left")
        ttk.Button(bf, text="Undo redirect", command=self.do_undo_redirect).pack(side="left", padx=6)
        ttk.Label(sf, style="Muted.TLabel", wraplength=720, justify="left",
                  text="Existing saves are moved to the new folder, and the game's 'save games' folder becomes a "
                       "link to it, so the game keeps working unchanged. Undo copies saves back and leaves the "
                       "custom folder untouched.").grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        uf = ttk.LabelFrame(f3, text="Move ALL user data (mods, saves, settings, logs)", padding=8)
        uf.grid(row=0, column=0, sticky="ew", pady=4)
        if meta:
            ttk.Label(uf, style="Muted.TLabel", wraplength=720, justify="left",
                      text="Not available for this game. Use the save-games redirect above, or point "
                           "'User data folder' at wherever the game keeps its data.").pack(anchor="w")
        else:
            ttk.Label(uf, style="Muted.TLabel", wraplength=720, justify="left",
                      text="Writes userdir.txt into the game install folder, which tells the game to use a different "
                           "user data folder. May need admin rights if the game is under Program Files, and a game "
                           "file verification in Steam can remove it.").pack(anchor="w")
            ub = ttk.Frame(uf)
            ub.pack(anchor="w", pady=(6, 0))
            ttk.Button(ub, text="Relocate with userdir.txt…", command=self.write_userdir).pack(side="left")
            ttk.Button(ub, text="Remove userdir.txt", command=self.remove_userdir).pack(side="left", padx=6)

        bb = ttk.Frame(outer)
        bb.pack(fill="x", pady=(10, 0))
        ttk.Button(bb, text="Cancel", command=w.destroy).pack(side="right", padx=4)
        ttk.Button(bb, text="Save", style="Accent.TButton", command=self.ok).pack(side="right")

        for var in self.v.values():
            var.trace_add("write", lambda *_: self.refresh_hints())
        self.refresh_hints()
        w.update_idletasks()
        w.geometry(f"+{app.root.winfo_rootx() + 60}+{app.root.winfo_rooty() + 40}")
        w.grab_set()

    def path_row(self, label, key, kind, extra=None):
        ttk.Label(self.f, text=label).grid(row=self.row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self.f, textvariable=self.v[key], width=70).grid(row=self.row, column=1, sticky="ew",
                                                                     padx=6, pady=(6, 0))
        ttk.Button(self.f, text="Browse…", command=lambda: self.browse(self.v[key], kind)).grid(
            row=self.row, column=2, pady=(6, 0))
        if extra:
            ttk.Button(self.f, text=extra[0], command=extra[1]).grid(row=self.row, column=3, padx=(4, 0), pady=(6, 0))
        self.row += 1

    def text_row(self, label, key):
        ttk.Label(self.f, text=label).grid(row=self.row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self.f, textvariable=self.v[key], width=30).grid(row=self.row, column=1, sticky="w",
                                                                     padx=6, pady=(6, 0))
        self.row += 1

    def hint(self, text):
        var = tk.StringVar()
        ttk.Label(self.f, textvariable=var, style="Muted.TLabel").grid(row=self.row, column=1, columnspan=3,
                                                                       sticky="w", padx=6)
        self.row += 1
        self.hints.append((var, text))

    def cur_ud(self):
        return self.v["user_dir"].get().strip() or default_user_dir(self.app.key, self.v["install_dir"].get().strip())

    def refresh_hints(self):
        for var, t in self.hints:
            var.set(t() if callable(t) else t)
        loc, linked = saves_location(self.cur_ud())
        self.saves_info.set(f"Currently: {loc}" + ("   (redirected)" if linked else "   (default)"))

    def browse(self, var, kind):
        start = var.get() or None
        if kind == "dir":
            p = filedialog.askdirectory(parent=self.w, initialdir=start)
        else:
            p = filedialog.askopenfilename(parent=self.w, initialdir=os.path.dirname(start) if start else None)
        if p:
            var.set(os.path.normpath(p))

    def detect(self):
        p = detect_install(self.app.key)
        if p:
            self.v["install_dir"].set(p)
        else:
            messagebox.showinfo(APP_NAME, "Couldn't find it in any Steam library. Use Browse… instead.", parent=self.w)

    def lib_add(self):
        d = filedialog.askdirectory(parent=self.w, title="Folder containing mods")
        if d and norm(d) not in map(norm, self.libs.get(0, "end")):
            self.libs.insert("end", os.path.normpath(d))

    def lib_remove(self):
        for i in reversed(self.libs.curselection()):
            self.libs.delete(i)

    def apply(self):
        g = self.app.g
        for k in self.FIELDS:
            g[k] = self.v[k].get().strip()
        g["libraries"] = list(self.libs.get(0, "end"))
        g["scan_workshop"] = bool(self.scan_ws.get())
        g["pre_launch_check"] = bool(self.precheck.get())
        g["saves_dir"] = self.saves_target.get().strip()
        self.app.save_cfg()

    def do_redirect(self):
        target = self.saves_target.get().strip()
        ud = self.cur_ud()
        if not target:
            messagebox.showerror(APP_NAME, "Choose the new saves folder first.", parent=self.w)
            return
        if not os.path.isdir(ud):
            messagebox.showerror(APP_NAME, f"User data folder not found:\n{ud}\nRun the game once, or set it above.",
                                 parent=self.w)
            return
        if not messagebox.askyesno(APP_NAME, f"Move saves from\n{os.path.join(ud, 'save games')}\nto\n{target}\n\n"
                                             "Make sure the game is closed.", parent=self.w):
            return
        try:
            n = redirect_saves(ud, target)
            self.apply()
            messagebox.showinfo(APP_NAME, f"Done. {n} item(s) moved. The game will now save to:\n{target}",
                                parent=self.w)
        except (OSError, shutil.Error) as e:
            messagebox.showerror(APP_NAME, f"Redirect failed:\n{e}", parent=self.w)
        self.refresh_hints()

    def do_undo_redirect(self):
        ud = self.cur_ud()
        if not is_link(os.path.join(ud, "save games")):
            messagebox.showinfo(APP_NAME, "Saves are not redirected.", parent=self.w)
            return
        try:
            n = undo_saves_redirect(ud)
            messagebox.showinfo(APP_NAME, f"Restored default save folder ({n} item(s) copied back).", parent=self.w)
        except (OSError, shutil.Error) as e:
            messagebox.showerror(APP_NAME, f"Undo failed:\n{e}", parent=self.w)
        self.refresh_hints()

    def write_userdir(self):
        inst = self.v["install_dir"].get().strip()
        if not inst or not os.path.isdir(inst):
            messagebox.showerror(APP_NAME, "Set the game install folder first.", parent=self.w)
            return
        target = filedialog.askdirectory(parent=self.w, title="New location for ALL user data")
        if not target:
            return
        target = os.path.normpath(target)
        old = self.cur_ud()
        try:
            if os.path.isdir(old) and norm(old) != norm(target) and messagebox.askyesno(
                    APP_NAME, f"Copy your existing user data from\n{old}\nto\n{target}?\n\n(Nothing is deleted.)",
                    parent=self.w):
                shutil.copytree(old, target, dirs_exist_ok=True, symlinks=True)
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(inst, "userdir.txt"), "w", encoding="utf-8") as fh:
                fh.write(target.replace("\\", "/") + "\n")
            self.v["user_dir"].set(target)
            self.apply()
            messagebox.showinfo(APP_NAME, "userdir.txt written. Start the game once and confirm a new save "
                                          "appears in the new folder.", parent=self.w)
        except (OSError, shutil.Error) as e:
            messagebox.showerror(APP_NAME, f"Failed:\n{e}\n\nIf this is a permissions error, the install folder "
                                           "is protected — use the save-games redirect instead.", parent=self.w)

    def remove_userdir(self):
        p = os.path.join(self.v["install_dir"].get().strip(), "userdir.txt")
        if not os.path.isfile(p):
            messagebox.showinfo(APP_NAME, "No userdir.txt present.", parent=self.w)
            return
        try:
            os.remove(p)
            self.v["user_dir"].set("")
            messagebox.showinfo(APP_NAME, "Removed. The game uses the default location again; your data in the "
                                          "custom folder was left untouched.", parent=self.w)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Failed:\n{e}", parent=self.w)

    def ok(self):
        inst = self.v["install_dir"].get().strip()
        if inst and not self.v["exe"].get().strip():
            self.v["exe"].set(detect_exe(self.app.key, inst))
        self.apply()
        self.w.destroy()
        self.app.refresh()


def cli_update(log_path):
    """Headless self-update: python paradox_mod_manager.py --update-now (logs to pmm_update.log)."""
    def log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    cfg = load_config()
    repo = cfg["ui"].get("update_repo") or UPDATE_REPO
    log(f"running {__version__}, checking {repo}")
    try:
        rel = latest_release(repo)
        tag = rel.get("tag_name", "")
        log(f"latest release {tag}")
        if not is_newer(tag, __version__):
            log("already up to date")
            return 0
        new = fetch_update(rel, None)
        log(f"downloaded {new} ({os.path.getsize(new)} bytes), installing and restarting")
        install_update_and_restart(new)
        return 0
    except Exception as e:
        log(f"FAILED: {e!r}")
        return 1


def main():
    if "--version" in sys.argv:
        print(__version__)
        return
    if "--update-now" in sys.argv:
        sys.exit(cli_update(os.path.join(app_dir(), "pmm_update.log")))
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    # leftovers from a previous self-update
    kind, target, _ = running_target()
    for leftover in (target + ".old",):
        if kind == "py" and os.path.isfile(leftover):
            try:
                os.remove(leftover)
            except OSError:
                pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
