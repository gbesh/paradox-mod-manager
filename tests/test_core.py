"""Tests for the non-GUI logic. Run:  python -m unittest discover -s tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import paradox_mod_manager as p  # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pmm_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def cfg(self, install, libraries=()):
        g = p.default_game_cfg()
        g.update(install_dir=install, libraries=list(libraries))
        return g


class TestClassic(Base):
    def setUp(self):
        super().setUp()
        self.install = self.path("steamapps", "common", "Crusader Kings III")
        write(os.path.join(self.install, "launcher", "launcher-settings.json"),
              json.dumps({"version": "1.16.2 (Corvus)", "rawVersion": "1.16.2"}))
        for fol, name in (("dlc001_preorder", "Garments"), ("dlc016_cp2", "West Slavic Attire")):
            write(os.path.join(self.install, "game", "dlc", fol, fol[:6] + ".dlc"),
                  f'name = "{name}"\nsteam_id = "1"\n')
        ws = self.path("steamapps", "workshop", "content", "1158310", "2216670956")
        write(os.path.join(ws, "descriptor.mod"),
              'name="Workshop Mod"\nversion="1.9"\nsupported_version="1.16.*"\nremote_file_id="2216670956"')
        self.user = self.path("docs", "CK3")
        write(os.path.join(self.user, "mod", "local", "descriptor.mod"), 'name="Local Old"\nsupported_version="1.12.4"')
        write(os.path.join(self.user, "mod", "local.mod"), 'name="Local Old"\npath="mod/local"\nsupported_version="1.12.4"')
        write(self.path("lib", "Barber", "descriptor.mod"),
              'name="Barber"\nversion="2"\nsupported_version="1.16.1"\ndependencies={ "Local Old" }')
        write(self.path("lib", "Barber", "common", "x.txt"), "a")
        write(os.path.join(self.user, "mod", "local", "common", "x.txt"), "b")

    def test_load_save_roundtrip(self):
        b = p.make_backend("ck3")
        st = b.load(self.cfg(self.install, [self.path("lib")]), self.user)
        names = sorted(m["name"] for m in st["mods"].values())
        self.assertEqual(names, ["Barber", "Local Old", "Workshop Mod"])
        self.assertEqual([d["id"] for d in st["dlcs"]],
                         ["dlc/dlc001_preorder/dlc001.dlc", "dlc/dlc016_cp2/dlc016.dlc"])
        barber = next(k for k, m in st["mods"].items() if m["name"] == "Barber")
        st["enabled"] = [barber, "mod/local.mod"]
        st["disabled"] = {"dlc/dlc016_cp2/dlc016.dlc"}
        b.save(self.user, st)
        data = load(os.path.join(self.user, "dlc_load.json"))
        self.assertEqual(data["enabled_mods"], [barber, "mod/local.mod"])
        self.assertEqual(data["disabled_dlcs"], ["dlc/dlc016_cp2/dlc016.dlc"])
        st2 = b.load(self.cfg(self.install, [self.path("lib")]), self.user)
        self.assertEqual(st2["enabled"], [barber, "mod/local.mod"])
        # dependency + sort + conflicts
        self.assertEqual(p.dependency_problems(st2["mods"], st2["enabled"]), {barber: ["load after 'Local Old'"]})
        fixed = p.sort_by_dependencies(st2["mods"], st2["enabled"])
        self.assertEqual(fixed, ["mod/local.mod", barber])
        summary, files = p.find_conflicts(st2["mods"], fixed)
        self.assertEqual(files, {"common/x.txt": fixed})
        self.assertEqual(summary[barber], {"wins": 1, "loses": 0})
        self.assertEqual(p.detect_game_version(self.install)[0], "1.16.2")

    def test_library_removed_cleans_descriptor(self):
        b = p.make_backend("ck3")
        b.load(self.cfg(self.install, [self.path("lib")]), self.user)
        self.assertTrue(any(f.startswith("pmm_") for f in os.listdir(os.path.join(self.user, "mod"))))
        b.load(self.cfg(self.install, []), self.user)
        self.assertFalse(any(f.startswith("pmm_Barber") for f in os.listdir(os.path.join(self.user, "mod"))))

    def test_flat_dlc_layout(self):
        inst = self.path("eu4")
        write(os.path.join(inst, "dlc", "dlc001.dlc"), 'name="Rights of Man"')
        self.assertEqual([d["id"] for d in p.scan_classic_dlcs(inst)], ["dlc/dlc001.dlc"])


class TestVic3(Base):
    def setUp(self):
        super().setUp()
        self.install = self.path("Victoria 3")
        write(os.path.join(self.install, "launcher", "launcher-settings.json"),
              json.dumps({"version": "1.13.6 (Matcha)", "rawVersion": "1.13.6"}))
        write(os.path.join(self.install, "game", "dlc", "dlc002_american_buildings", "dlc002.dlc"), 'name="x"')
        write(os.path.join(self.install, "game", "dlc", "dlc002_american_buildings",
                           "dlc002_american_buildings.dlc.json"),
              json.dumps({"id": {"steam": "2071471", "paradox": "dlc002_american_buildings"},
                          "displayName": {"en": "American Buildings Pack"}}))
        self.lib = self.path("Vicky 3 mods")
        for fol, name, ver in (("2880441881 The Great Rework", "The Great Rework", "1.7.17"),
                               ("2955438049 Competent Auto Expand", "Competent Auto Expand", "1.0")):
            write(os.path.join(self.lib, fol, ".metadata", "metadata.json"),
                  json.dumps({"name": name, "id": "", "version": ver, "supported_game_version": "1.13.*",
                              "relationships": []}))
        self.user = self.path("user", "Victoria 3")
        # real-world file from the user's machine (Windows path, may not exist here)
        write(os.path.join(self.user, "content_load.json"), json.dumps(
            {"disabledDLC": [], "enabledMods": [{"path": os.path.join(self.lib, "2955438049 Competent Auto Expand")},
                                                {"path": "D:\\mod\\gone\\mod"}]}))

    def test_roundtrip(self):
        b = p.make_backend("vic3")
        st = b.load(self.cfg(self.install, [self.lib]), self.user)
        self.assertEqual([st["mods"][m]["name"] for m in st["enabled"]], ["Competent Auto Expand", "mod"])
        self.assertEqual(st["mods"][st["enabled"][1]]["source"], "Missing")
        self.assertEqual(st["mods"][st["enabled"][0]]["remote_id"], "2955438049")
        self.assertEqual([(d["id"], d["name"]) for d in st["dlcs"]],
                         [("dlc002_american_buildings", "American Buildings Pack")])
        rework = p.norm(os.path.join(self.lib, "2880441881 The Great Rework"))
        st["enabled"] = [rework, st["enabled"][0]]
        st["disabled"] = {"dlc002_american_buildings"}
        b.save(self.user, st)
        data = load(os.path.join(self.user, "content_load.json"))
        self.assertEqual([os.path.basename(e["path"]) for e in data["enabledMods"]],
                         ["2880441881 The Great Rework", "2955438049 Competent Auto Expand"])
        self.assertEqual(data["disabledDLC"], [{"paradoxAppId": "dlc002_american_buildings"}])
        self.assertTrue(os.path.isfile(os.path.join(self.user, "content_load.json.bak")))
        st2 = b.load(self.cfg(self.install, [self.lib]), self.user)
        self.assertEqual(st2["enabled"], st["enabled"])
        self.assertEqual(st2["disabled"], {"dlc002_american_buildings"})


class TestEU5(Base):
    PLAYSETS = {"file_version": "1.0.0", "playsets": [{
        "name": "Initial playset", "isActive": True, "isAutomaticallySorted": True, "orderedListMods": [],
        "DLC": [{"paradoxAppId": "Ancient Monument Pack", "isEnabled": True},
                {"paradoxAppId": "Shared DLC Data", "isEnabled": True},
                {"paradoxAppId": "Sacred Sites", "isEnabled": True}]}]}

    def setUp(self):
        super().setUp()
        self.install = self.path("Europa Universalis V")
        for fol, hidden in (("D000_shared", True), ("D008_fate_of_the_phoenix", False),
                            ("D015_ancient_monuments_pack", False), ("D017_sacred_sites_pack", False)):
            write(os.path.join(self.install, "game", "dlc", fol, fol + ".dlc.json"),
                  json.dumps({"name": fol.lower(), "path": "dlc/" + fol, "steam_id": 0 if hidden else 3699010,
                              "hidden": hidden}))
        self.user = self.path("user", "EUV")
        write(os.path.join(self.user, "playsets.json"), json.dumps(self.PLAYSETS))
        write(os.path.join(self.user, "continue_game.json"), json.dumps({"rawGameVersion": "1.0.7"}))
        self.mods = self.path("user", "EUV", "mod")
        for fol in ("alpha", "beta"):
            write(os.path.join(self.mods, fol, ".metadata", "metadata.json"),
                  json.dumps({"name": fol.title(), "id": "com." + fol, "version": "1",
                              "supported_game_version": "1.0.*",
                              "relationships": [{"rel_type": "dependency", "id": "com.alpha",
                                                 "display_name": "Alpha"}] if fol == "beta" else []}))

    def test_roundtrip(self):
        b = p.make_backend("eu5")
        st = b.load(self.cfg(self.install), self.user)
        self.assertEqual(st["enabled"], [])
        ids = [d["id"] for d in st["dlcs"]]
        # existing playset entries keep their labels; hidden shared data matched loosely or listed;
        # Fate of the Phoenix is new and appears under its own name
        self.assertIn("Ancient Monument Pack", ids)
        self.assertIn("Sacred Sites", ids)
        self.assertIn("d008_fate_of_the_phoenix", ids)
        self.assertEqual(p.detect_game_version(self.install, self.user), ("1.0.7", "1.0.7 (last played)"))
        alpha, beta = (p.norm(os.path.join(self.mods, f)) for f in ("alpha", "beta"))
        st["enabled"] = [beta, alpha]
        self.assertEqual(p.dependency_problems(st["mods"], st["enabled"]), {beta: ["load after 'Alpha'"]})
        st["enabled"] = p.sort_by_dependencies(st["mods"], st["enabled"])
        self.assertEqual(st["enabled"], [alpha, beta])
        st["disabled"] = {"Sacred Sites", "d008_fate_of_the_phoenix"}
        b.save(self.user, st)
        data = load(os.path.join(self.user, "playsets.json"))
        ps = data["playsets"][0]
        self.assertEqual([os.path.basename(e["path"]) for e in ps["orderedListMods"]], ["alpha", "beta"])
        self.assertTrue(all(e["isEnabled"] for e in ps["orderedListMods"]))
        self.assertFalse(ps["isAutomaticallySorted"])
        dlc = {e["paradoxAppId"]: e["isEnabled"] for e in ps["DLC"]}
        self.assertEqual(dlc, {"Ancient Monument Pack": True, "Shared DLC Data": True, "Sacred Sites": False,
                               "d008_fate_of_the_phoenix": False})
        # disabling a mod keeps it listed as isEnabled false
        st["enabled"] = [alpha]
        b.save(self.user, st)
        ps = load(os.path.join(self.user, "playsets.json"))["playsets"][0]
        self.assertEqual([(os.path.basename(e["path"]), e["isEnabled"]) for e in ps["orderedListMods"]],
                         [("alpha", True), ("beta", False)])
        st2 = b.load(self.cfg(self.install), self.user)
        self.assertEqual(st2["enabled"], [alpha])
        self.assertEqual(st2["disabled"], {"Sacred Sites", "d008_fate_of_the_phoenix"})

    def test_missing_playsets_file(self):
        os.remove(os.path.join(self.user, "playsets.json"))
        b = p.make_backend("eu5")
        st = b.load(self.cfg(self.install), self.user)
        st["enabled"] = [p.norm(os.path.join(self.mods, "alpha"))]
        b.save(self.user, st)
        ps = load(os.path.join(self.user, "playsets.json"))["playsets"][0]
        self.assertTrue(ps["isActive"])
        self.assertEqual(len(ps["orderedListMods"]), 1)


class TestMisc(Base):
    def test_compat(self):
        self.assertEqual(p.compat_status("1.16.*", "1.16.2")[0], "ok")
        self.assertEqual(p.compat_status("1.16.1", "1.16.2")[0], "minor")
        self.assertEqual(p.compat_status("v1.15.0", "1.16.2")[0], "old")
        self.assertEqual(p.compat_status("1.17", "1.16.2")[0], "new")
        self.assertEqual(p.compat_status("", "1")[0], "unknown")

    def test_versions(self):
        self.assertTrue(p.is_newer("v2.0.1", "2.0.0"))
        self.assertFalse(p.is_newer("v2.0.0", "2.0.0"))
        self.assertTrue(p.is_newer("2.10.0", "2.9.9"))

    def test_export_import(self):
        mods = {"a": p.new_mod("a", name="Alpha", source="Local", remote_id="123", path="/x/a", exists=True),
                "b": p.new_mod("b", name="Beta", source="Local", path="/x/b", exists=True)}
        data = json.loads(json.dumps(p.export_load_order("ck3", mods, ["b", "a"], {"dlc/x"})))
        other = {"z": p.new_mod("z", name="Alpha renamed", source="Workshop", remote_id="123", exists=True),
                 "y": p.new_mod("y", name="Beta", source="Local", exists=True)}
        self.assertEqual(p.import_load_order(data, other), (["y", "z"], []))

    def test_saves_redirect_and_backup(self):
        user = self.path("u")
        write(os.path.join(user, "save games", "a.ck3"), "x")
        target = self.path("MySaves")
        self.assertEqual(p.redirect_saves(user, target), 1)
        self.assertEqual(p.saves_location(user), (os.path.realpath(target), True))
        z = self.path("b.zip")
        self.assertEqual(p.backup_saves(p.saves_location(user)[0], z), 1)
        self.assertTrue(os.path.isfile(z))
        self.assertEqual(p.undo_saves_redirect(user), 1)
        self.assertFalse(p.saves_location(user)[1])


if __name__ == "__main__":
    unittest.main()
