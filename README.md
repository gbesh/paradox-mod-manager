# Paradox Mod Manager

An offline mod and DLC manager for Paradox grand strategy games. No account, no login and no Paradox launcher needed.

| Game | Mods | DLC on/off | File it writes |
|---|---|---|---|
| Crusader Kings III | ✔ | ✔ | `dlc_load.json` |
| Europa Universalis IV | ✔ | ✔ | `dlc_load.json` |
| Europa Universalis V | ✔ | ✔ | `playsets.json` (the active playset) |
| Hearts of Iron IV | ✔ | ✔ | `dlc_load.json` |
| Imperator: Rome | ✔ | ✔ | `dlc_load.json` |
| Stellaris | ✔ | ✔ | `dlc_load.json` |
| Victoria 3 | ✔ | ✔ | `content_load.json` |

## Download

Get **`ParadoxModManager.exe`** from the [latest release](../../releases/latest) and run it. There's nothing to install. Settings are saved in `pmm_config.json` next to the .exe, so keep the two together.

The app checks for updates when it starts. When a newer release exists, click **Update now** and it downloads the new version, checks its SHA-256, replaces itself and restarts. You can turn this off under **Help → Check for updates on start**.

## Features

- **You choose every location:** game install, executable and user-data folder. Steam detection is optional.
- **Mod library folders:** keep mods anywhere on disk and they're picked up automatically. Steam Workshop downloads are read directly.
- **Versions at a glance:** game version, each mod's version, and the game version each mod was made for, with a compatibility verdict.
- **Load order:**
  - drag and drop, up/down/top/bottom, right-click menu
  - one-click **Sort by dependencies**
  - **file-conflict checker** showing which mod wins each overlapping file
- **Pre-launch check:** warns about missing or misordered dependencies, outdated mods and missing files before you start the game.
- **DLC tab:** switch individual DLC off or on.
- **Playsets:** save and load your own playsets (mods and DLC), export and import load orders as files, copy the load order as text, and **undo the last save**.
- **Save games:** move the save folder anywhere (the game keeps working through a folder link), or move all user data with `userdir.txt` (classic games). One-click zip **backup of your saves**.
- Dark mode, and it remembers the window size and the last tab you used.

## How it works

The app only edits the same small load files the official launcher uses (see the table), and keeps a `.bak` copy of each before saving. For classic games it registers mods from your library folders by writing small `.mod` files into `<user data>/mod`. Those files start with `# pmm-managed`, and they're the only files it ever deletes. Mod folders themselves are never touched.

**EU5:** the app edits the playset marked active in `playsets.json`, which is the one the game loads. It switches off the game's automatic sorting so your load order is kept.

## Running from source

Python 3.8+ with Tkinter (included in the python.org Windows installer):

```
python paradox_mod_manager.py
```

Run the tests with `python -m unittest discover -s tests`. To build the .exe yourself, run `build_exe.bat`.

## Releasing (maintainers)

1. Bump `__version__` in `paradox_mod_manager.py` and add a section to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v2.0.1 && git push origin main v2.0.1`.
3. GitHub Actions runs the tests, builds the .exe on Windows and publishes the release with checksums. Installed copies then offer the update.
