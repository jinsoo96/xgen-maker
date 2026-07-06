import unittest
from unittest.mock import patch

from xgen_maker import sdk_check


class TestSdkCheck(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(sdk_check._ver_tuple("1.29.1"), (1, 29, 1))
        self.assertGreater(sdk_check._ver_tuple("1.29.1"), sdk_check._ver_tuple("1.19.0"))
        self.assertEqual(sdk_check._ver_tuple("bad"), (0,))

    def test_contract_probe_present(self):
        # 엔진이 설치돼 있으면 계약이 온전해야 함(현재 환경)
        probe = sdk_check.contract_probe()
        if probe["engine"]:
            self.assertEqual(probe["missing"], [])
            self.assertIn("Stage", probe["present"])
            self.assertIn("PipelineState.user_input", probe["present"])

    def test_self_check_verdict_drift(self):
        with patch.object(sdk_check, "installed_versions",
                          return_value={"xgen-sdk": "1.19.0", "xgen-harness": "1.23.0"}), \
             patch.object(sdk_check, "latest_versions",
                          return_value={"xgen-sdk": "1.29.1", "xgen-harness": "1.27.0"}), \
             patch.object(sdk_check, "contract_probe",
                          return_value={"ok": True, "engine": "x", "present": [], "missing": [],
                                        "sandbox_ok": True}):
            r = sdk_check.self_check()
        self.assertEqual(r["verdict"], "drift")
        self.assertTrue(r["drift"]["xgen-sdk"]["behind"])

    def test_self_check_verdict_broken(self):
        with patch.object(sdk_check, "installed_versions", return_value={"xgen-sdk": "1.29.1"}), \
             patch.object(sdk_check, "latest_versions", return_value={"xgen-sdk": "1.29.1"}), \
             patch.object(sdk_check, "contract_probe",
                          return_value={"ok": False, "engine": "x", "present": [],
                                        "missing": ["Stage"], "sandbox_ok": False}):
            r = sdk_check.self_check()
        self.assertEqual(r["verdict"], "broken")

    def test_self_check_verdict_ok(self):
        with patch.object(sdk_check, "installed_versions", return_value={"xgen-sdk": "1.29.1"}), \
             patch.object(sdk_check, "latest_versions", return_value={"xgen-sdk": "1.29.1"}), \
             patch.object(sdk_check, "contract_probe",
                          return_value={"ok": True, "engine": "x", "present": [], "missing": [],
                                        "sandbox_ok": True}):
            r = sdk_check.self_check()
        self.assertEqual(r["verdict"], "ok")

    def test_maker_catalog(self):
        cat = sdk_check.maker_catalog()
        self.assertEqual(cat["name"], "xgen-maker")
        self.assertIn("kg", cat["capabilities"])
        self.assertIn("safety", cat["capabilities"])


if __name__ == "__main__":
    unittest.main()
