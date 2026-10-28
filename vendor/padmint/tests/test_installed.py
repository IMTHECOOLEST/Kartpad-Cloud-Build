"""PadMint tells which games are on this Mac and on paired iPhones and iPads."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from padmint import installed

FAKE = r'''#!/bin/sh
out=""; prev=""
for a in "$@"; do [ "$prev" = "--json-output" ] && out="$a"; prev="$a"; done
case "$*" in
  *"list devices"*) cat "$FIXTURES/devices.json" > "$out" ;;
  *IPAD*) cat "$FIXTURES/apps.json" > "$out" ;;
  *) exit 1 ;;
esac
'''


class InstalledTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "tools").mkdir()
        patcher = mock.patch.object(installed.tools, "tools_root", lambda: self.home / "tools")
        patcher.start()
        self.addCleanup(patcher.stop)

    def device(self, udid, kind):
        return {"deviceProperties": {"name": udid.title()},
                "hardwareProperties": {"udid": udid, "reality": "physical", "platform": "iOS", "deviceType": kind},
                "connectionProperties": {"pairingState": "paired"}}

    def test_a_reachable_ipad_is_read_and_an_away_iphone_keeps_what_was_seen(self):
        fixtures = self.home / "fixtures"
        fixtures.mkdir()
        (fixtures / "devices.json").write_text(json.dumps({"result": {"devices": [
            self.device("IPAD", "iPad"), self.device("PHONE", "iPhone"),
            dict(self.device("SIM", "iPhone"), hardwareProperties={"udid": "SIM", "reality": "simulated", "platform": "iOS"})]}}))
        (fixtures / "apps.json").write_text(json.dumps({"result": {"apps": [
            {"name": "BlueWake", "version": "0.8.0"}, {"name": "OpenRCT2 Touch", "version": "0.1.0"},
            {"name": "Notes", "version": "1.0"}]}}))
        (self.home / "devices.json").write_text(json.dumps({"PHONE": {"id": "PHONE", "name": "Phone", "kind": "iphone",
                                                                     "checked": 100, "apps": [{"game": "kartpad", "version": "0.7.9"}]}}))
        tool = self.home / "devicectl"
        tool.write_text(FAKE)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"FIXTURES": str(fixtures)}):
            found = {d["id"]: d for d in installed.phones(str(tool), installed.names())}
        self.assertEqual(set(found), {"IPAD", "PHONE"})
        self.assertTrue(found["IPAD"]["reachable"])
        self.assertEqual(found["IPAD"]["kind"], "ipad")
        self.assertEqual(sorted(a["game"] for a in found["IPAD"]["apps"]), ["bluewake", "openrct2touch"])
        self.assertFalse(found["PHONE"]["reachable"])
        self.assertEqual(found["PHONE"]["apps"], [{"game": "kartpad", "version": "0.7.9"}])
        self.assertEqual(found["PHONE"]["checked"], 100)
        saved = json.loads((self.home / "devices.json").read_text())
        self.assertNotIn("reachable", saved["IPAD"])
        self.assertNotIn("Notes", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
