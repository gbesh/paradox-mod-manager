"""Headless GUI smoke test (needs a display, e.g. xvfb-run). Not part of the unit tests.
usage: python gui_driver.py <workdir> <path-to-paradox_mod_manager.py>"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

work, src = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
sys.argv = [os.path.join(work, "driver.py")]  # config lands in work/


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


W = lambda *a: os.path.join(work, *a)  # noqa: E731
# ---- CK3 (classic)
ck = W("lib", "steamapps", "common", "Crusader Kings III")
write(os.path.join(ck, "launcher", "launcher-settings.json"), json.dumps({"version": "1.16.2 (Corvus)", "rawVersion": "1.16.2"}))
write(os.path.join(ck, "binaries", "ck3"), "#!/bin/sh\n")
for fol, name in (("dlc001_preorder", "Garments of the HRE"), ("dlc016_cp2", "West Slavic Attire"), ("dlc022_ep4", "All Under Heaven")):
    write(os.path.join(ck, "game", "dlc", fol, fol[:6] + ".dlc"), f'name = "{name}"\nsteam_id = "12"\n')
ws = W("lib", "steamapps", "workshop", "content", "1158310", "2216670956")
write(os.path.join(ws, "descriptor.mod"), 'name="More Bookmarks+"\nversion="1.9"\nsupported_version="1.16.*"\nremote_file_id="2216670956"')
write(os.path.join(ws, "common", "bookmarks", "00_bookmarks.txt"), "a")
ckud = W("docs", "CK3")
write(os.path.join(ckud, "mod", "agot", "descriptor.mod"), 'name="A Game of Thrones"\nversion="0.5"\nsupported_version="1.16.*"')
write(os.path.join(ckud, "mod", "agot.mod"), 'name="A Game of Thrones"\npath="mod/agot"\nversion="0.5"\nsupported_version="1.16.*"')
for i in range(40):
    write(os.path.join(ckud, "mod", "agot", "common", "bookmarks", f"f{i}.txt"), "x")
write(os.path.join(ckud, "mod", "agot", "common", "bookmarks", "00_bookmarks.txt"), "x")
write(W("MyMods", "AGOT+", "descriptor.mod"), 'name="AGOT+"\nversion="1.0.0"\nsupported_version="1.15.0"\ndependencies={ "A Game of Thrones" }')
for i in range(5):
    write(W("MyMods", "AGOT+", "common", "bookmarks", f"f{i}.txt"), "y")
write(W("MyMods", "Old Thing", "descriptor.mod"), 'name="Old Thing"\nversion="3"\nsupported_version="1.9.*"')
write(os.path.join(ckud, "dlc_load.json"), json.dumps({"enabled_mods": ["mod/agot.mod"], "disabled_dlcs": []}))
write(os.path.join(ckud, "save games", "Brenainn.ck3"), "save")
# ---- Victoria 3
v3 = W("Victoria 3")
write(os.path.join(v3, "launcher", "launcher-settings.json"), json.dumps({"version": "1.13.6 (Matcha)", "rawVersion": "1.13.6"}))
for fol, nm in (("dlc002_american_buildings", "American Buildings Pack"), ("dlc010_ep1", "Sphere of Influence")):
    write(os.path.join(v3, "game", "dlc", fol, fol + ".dlc.json"),
          json.dumps({"id": {"steam": "2071471", "paradox": fol}, "displayName": {"en": nm}}))
v3lib = W("Vicky 3 mods")
write(os.path.join(v3lib, "2880441881 The Great Rework", ".metadata", "metadata.json"),
      json.dumps({"name": "The Great Rework", "version": "1.7.17", "supported_game_version": "1.13.*"}))
v3ud = W("docs", "Victoria 3")
write(os.path.join(v3ud, "content_load.json"),
      json.dumps({"disabledDLC": [], "enabledMods": [{"path": "D:\\mod\\Vicky 3 Mods\\2955438049 Competent Auto Expand"}]}))
# ---- EU5
eu5 = W("Europa Universalis V")
write(os.path.join(eu5, "binaries", "eu5.exe"), "")
for fol, hidden in (("D000_shared", True), ("D008_fate_of_the_phoenix", False), ("D015_ancient_monuments_pack", False),
                    ("D017_sacred_sites_pack", False)):
    write(os.path.join(eu5, "game", "dlc", fol, fol + ".dlc.json"),
          json.dumps({"name": fol.lower(), "steam_id": 0 if hidden else 3699010, "hidden": hidden}))
eu5ud = W("docs", "EUV")
write(os.path.join(eu5ud, "playsets.json"), json.dumps({"file_version": "1.0.0", "playsets": [{
    "name": "Initial playset", "isActive": True, "isAutomaticallySorted": True, "orderedListMods": [],
    "DLC": [{"paradoxAppId": "Ancient Monument Pack", "isEnabled": True},
            {"paradoxAppId": "Shared DLC Data", "isEnabled": True},
            {"paradoxAppId": "Sacred Sites", "isEnabled": True}]}]}))
write(os.path.join(eu5ud, "continue_game.json"), json.dumps({"rawGameVersion": "1.0.7"}))
write(os.path.join(eu5ud, "mod", "Better Trade", ".metadata", "metadata.json"),
      json.dumps({"name": "Better Trade", "id": "bt", "version": "2.1", "supported_game_version": "1.0.*"}))

cfg = {"current_game": "ck3", "ui": {"geometry": "1320x800+0+0"}, "games": {
    "ck3": {"install_dir": ck, "user_dir": ckud, "libraries": [W("MyMods")]},
    "vic3": {"install_dir": v3, "user_dir": v3ud, "libraries": [v3lib]},
    "eu5": {"install_dir": eu5, "user_dir": eu5ud, "libraries": []}}}
write(W("pmm_config.json"), json.dumps(cfg))

spec = importlib.util.spec_from_file_location("pmm", src)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

# --- stubs: dialogs auto-answer, fake GitHub release
log = []
p.messagebox.askyesnocancel = lambda *a, **k: (log.append(("askyesnocancel", a[1])), False)[1]
p.messagebox.askyesno = lambda *a, **k: (log.append(("askyesno", a[1])), True)[1]
p.messagebox.showinfo = lambda *a, **k: log.append(("info", a[1]))
p.messagebox.showwarning = lambda *a, **k: log.append(("warn", a[1]))
p.messagebox.showerror = lambda *a, **k: log.append(("error", a[1]))
p.latest_release = lambda repo: {"tag_name": "v2.1.0", "body": "- New stuff\n- Fixes", "assets": []}
export_path = W("export.json")
p.filedialog.asksaveasfilename = lambda **k: export_path if k.get("defaultextension") == ".json" else W("saves.zip")
p.filedialog.askopenfilename = lambda **k: export_path

root = p.tk.Tk()
app = p.App(root)
shots = []
results = {}


def shot(name):
    root.update()
    path = W(f"shot_{len(shots):02d}_{name}.png")
    subprocess.run(["import", "-window", "root", path], check=False)
    shots.append(path)


def check(cond, msg):
    results[msg] = bool(cond)
    print(("PASS " if cond else "FAIL ") + msg, flush=True)


steps = []


def step(fn):
    steps.append(fn)
    return fn


@step
def s_ck3_initial():
    names = {m["name"] for m in app.mods.values()}
    check(names == {"A Game of Thrones", "More Bookmarks+", "AGOT+", "Old Thing"}, "CK3 mods discovered")
    check(len(app.dlcs) == 3, "CK3 DLC listed")
    check(app.banner.winfo_ismapped(), "update banner shown for fake v2.1.0")
    agotp = next(k for k, m in app.mods.items() if m["name"] == "AGOT+")
    bm = next(k for k, m in app.mods.items() if m["name"] == "More Bookmarks+")
    app.tree.selection_set([agotp, bm])
    app.toggle()
    app.enabled = [agotp, "mod/agot.mod", bm]  # deliberately wrong order
    app._changed(rescan=True)


@step
def s_wait_conflicts():
    pass


@step
def s_conflicts():
    agotp = app.enabled[0]
    check(app.conf_summary.get(agotp) == {"wins": 0, "loses": 5}, f"conflicts AGOT+ {app.conf_summary.get(agotp)}")
    check(app.conf_summary.get(app.enabled[2]) == {"wins": 1, "loses": 0}, "conflict winner is last in order")
    check("load after" in app.tree.set(agotp, "notes"), "dependency order warning shown")
    shot("ck3_mods")
    app.sort_deps()
    check(app.mods[app.enabled[0]]["name"] == "A Game of Thrones", "sort by dependencies fixed order")
    check(app.conf_summary.get(app.enabled[1]) == {"wins": 5, "loses": 0}, f"conflicts recomputed {app.conf_summary}")
    app.show_conflicts(app.enabled[1])


@step
def s_conflict_window():
    shot("conflicts_window")
    for w in root.winfo_children():
        if isinstance(w, p.tk.Toplevel):
            w.destroy()
    probs = p.launch_problems(app.mods, app.enabled, app.game_raw)
    check(any("Outdated" in x for x in probs), f"pre-launch finds outdated mod: {probs}")
    app.export_order()
    check(os.path.isfile(export_path), "export written")
    app.save_order()
    data = json.load(open(os.path.join(ckud, "dlc_load.json")))
    check(len(data["enabled_mods"]) == 3, "CK3 save wrote 3 mods")
    app.set_all(False)
    app.save_order()
    app.undo_last_save()
    check(len(app.enabled) == 3, "undo last save restored 3 mods")
    app.set_all(False)
    app.import_order()
    check(len(app.enabled) == 3, "import restored 3 mods")
    app.nb.select(1)
    app.dtree.selection_set("dlc/dlc016_cp2/dlc016.dlc")
    app.toggle()
    app.save_order()
    check(json.load(open(os.path.join(ckud, "dlc_load.json")))["disabled_dlcs"] == ["dlc/dlc016_cp2/dlc016.dlc"],
          "CK3 DLC disabled saved")
    shot("ck3_dlc")
    app.nb.select(0)
    app.dark_var.set(True)
    app.toggle_dark()


@step
def s_dark():
    shot("ck3_dark")
    app.game_combo.set("Victoria 3")
    app.on_game_change()


@step
def s_vic3():
    check(app.key == "vic3", "switched to Victoria 3")
    names = [app.mods[m]["name"] for m in app.enabled]
    check(names == ["2955438049 Competent Auto Expand"], f"V3 reads enabled (missing) mod: {names}")
    check([d["name"] for d in app.dlcs] == ["American Buildings Pack", "Sphere of Influence"], "V3 DLC names")
    rework = next(k for k, m in app.mods.items() if m["name"] == "The Great Rework")
    app.tree.selection_set(rework)
    app.toggle()
    app.set_selected(False, [app.enabled[0]])  # drop the missing one
    app.nb.select(1)
    app.dtree.selection_set("dlc010_ep1")
    app.toggle()
    app.nb.select(0)
    shot("vic3")
    app.save_order()
    d = json.load(open(os.path.join(v3ud, "content_load.json")))
    check([os.path.basename(e["path"]) for e in d["enabledMods"]] == ["2880441881 The Great Rework"], f"V3 saved mods {d}")
    check(d["disabledDLC"] == [{"paradoxAppId": "dlc010_ep1"}], "V3 saved DLC")
    app.game_combo.set("Europa Universalis V")
    app.on_game_change()


@step
def s_eu5():
    check(app.key == "eu5", "switched to EU5")
    check(app.game_disp == "1.0.7 (last played)", f"EU5 version {app.game_disp}")
    check("Initial playset" in app.note, "EU5 playset name shown")
    bt = next(k for k, m in app.mods.items() if m["name"] == "Better Trade")
    app.tree.selection_set(bt)
    app.toggle()
    app.nb.select(1)
    app.dtree.selection_set("Sacred Sites")
    app.toggle()
    shot("eu5_dlc")
    app.nb.select(0)
    shot("eu5_mods")
    app.save_order()
    ps = json.load(open(os.path.join(eu5ud, "playsets.json")))["playsets"][0]
    check([e["isEnabled"] for e in ps["orderedListMods"]] == [True], "EU5 mod saved")
    check({e["paradoxAppId"]: e["isEnabled"] for e in ps["DLC"]}["Sacred Sites"] is False, "EU5 DLC saved")
    app.open_settings()


@step
def s_settings():
    shot("eu5_settings")
    for w in root.winfo_children():
        if isinstance(w, p.tk.Toplevel):
            w.destroy()
    app.dark_var.set(False)
    app.toggle_dark()
    app.game_combo.set("Crusader Kings III")
    app.on_game_change()
    app.backup_saves_ui()


@step
def s_backup():
    root.geometry("1000x700+0+0")  # the minimum size: side panel must still fit


@step
def s_small():
    shot("min_size")
    launch_btn = next(w for w in app.save_btn.master.winfo_children() if "Launch" in str(w.cget("text")))
    check(launch_btn.winfo_ismapped() and launch_btn.winfo_y() > 0, "launch button visible at minimum size")
    side = app.save_btn.master
    lowest = max(w.winfo_y() + w.winfo_height() for w in side.winfo_children()
                 if w not in (app.save_btn, launch_btn) and w.winfo_ismapped())
    check(lowest <= app.save_btn.winfo_y(), f"side panel not overlapping Save ({lowest} <= {app.save_btn.winfo_y()})")
    root.geometry("1320x800+0+0")


@step
def s_end():
    check(os.path.isfile(W("saves.zip")), "save backup zip written")
    shot("ck3_light_end")
    print("DIALOG LOG:", log)
    print("RESULT", sum(results.values()), "/", len(results))
    root.destroy()


def run(i=0):
    if i < len(steps):
        try:
            steps[i]()
        except Exception as e:  # report and continue
            import traceback
            traceback.print_exc()
            check(False, f"step {steps[i].__name__} raised {e}")
        root.after(1200, lambda: run(i + 1))


root.after(2500, run)
root.mainloop()
