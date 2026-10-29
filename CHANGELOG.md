# Changelog

## 2.0.0

- **New games:** Europa Universalis V (edits the active playset in `playsets.json`) and Victoria 3 (`content_load.json`), including their DLC.
- **Auto-update:** the app checks GitHub releases on start, then downloads, verifies and installs updates itself.
- **File-conflict checker:** a column per mod ("wins 12 · loses 3") and a detail window listing every overlapping file and which mod wins it.
- **Load order tools:**
  - drag-and-drop reordering
  - right-click menu with Steam Workshop page, open folder and copy name or path
  - **Sort by dependencies**
- **Pre-launch check** for missing or misordered dependencies, outdated mods and missing files, with "Fix order & launch".
- **Load orders:** export and import as a file, copy as text, and **undo last save**.
- **Save-game backups:** zip the whole saves folder in one click.
- Dark mode, sortable columns, remembered window size and tab, and a tabbed Settings window.
- Mods that are enabled but no longer installed are shown as "Not installed" instead of being silently dropped.

## 1.1.0

- DLC tab: switch individual DLC on or off for every supported game.

## 1.0.0

- First version: CK3, EU4, HOI4, Stellaris and Imperator.
  - mod library folders and save-folder redirect
  - version and compatibility columns
  - playsets
  - direct launch
