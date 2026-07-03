import json
import os
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.loop.deploy import plan_deploy, trigger_deploy
from xgen_maker.loop.verify import http_reachable, verify


class TestDeployInterlock(unittest.TestCase):
    def setUp(self):
        self.config = MakerConfig(
            gitlab_projects={"demo": "xgen2.0/demo"},
            target_branch="develop", deploy_env="dev")

    def test_plan_contains_request_but_sends_nothing(self):
        plan = plan_deploy(self.config, "demo", "fix/x", "http://mr/1")
        self.assertEqual(plan["env"], "dev")
        self.assertIn("/api/v4/projects/", plan["request"]["url"])
        self.assertEqual(plan["request"]["body"]["ref"], "develop")
        self.assertIn("머지 후", plan["precondition"])

    def test_off_mode(self):
        self.config.deploy_mode = "off"
        result = trigger_deploy(self.config, plan_deploy(self.config, "demo", "fix/x"))
        self.assertEqual(result["status"], "off")
        self.assertFalse(result["sent"])

    def test_dry_run_records_plan_no_send(self):
        self.config.deploy_mode = "dry_run"
        result = trigger_deploy(self.config, plan_deploy(self.config, "demo", "fix/x"))
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["sent"])
        self.assertIn("plan", result)

    def test_live_refused_without_interlock(self):
        self.config.deploy_mode = "live"
        os.environ.pop("XGEN_MAKER_DEPLOY_LIVE", None)
        result = trigger_deploy(self.config, plan_deploy(self.config, "demo", "fix/x"))
        self.assertEqual(result["status"], "refused")
        self.assertFalse(result["sent"])
        self.assertIn("인터록", result["reason"])


class TestVerifyReuse(unittest.TestCase):
    def test_unreachable_records_note_not_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = verify(True, ["xgen-frontend"], Path(tmp),
                            preview_base="http://127.0.0.1:59999")
        self.assertFalse(report["preview_reachable"])
        self.assertIn("자동 기동", report["note"])

    def test_http_reachable_false_on_closed_port(self):
        self.assertFalse(http_reachable("http://127.0.0.1:59999", timeout=2))


class TestConfigPathResolution(unittest.TestCase):
    def test_relative_paths_resolved_from_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "cfg.json"
            config_path.write_text(json.dumps(
                {"kg_path": "kg/m.json", "worklogs_dir": "worklogs"}), encoding="utf-8")
            config = MakerConfig.from_file(config_path)
        self.assertTrue(Path(config.kg_path).is_absolute())
        self.assertTrue(config.kg_path.endswith("m.json"))
        self.assertTrue(Path(config.worklogs_dir).is_absolute())


if __name__ == "__main__":
    unittest.main()
