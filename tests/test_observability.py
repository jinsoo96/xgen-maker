import io
import json
import os
import unittest
from unittest.mock import patch

from xgen_maker.loop import jenkins, argocd
from xgen_maker.loop.release import ladder, stage_url


class TestJenkinsReadonly(unittest.TestCase):
    def setUp(self):
        for k in ("XGEN_MAKER_JENKINS_URL", "XGEN_MAKER_JENKINS_USER",
                  "XGEN_MAKER_JENKINS_TOKEN"):
            os.environ.pop(k, None)

    def test_unavailable_without_creds(self):
        self.assertFalse(jenkins.available())
        self.assertEqual(jenkins.list_jobs(), [])

    def test_env_of_handles_suffix(self):
        self.assertEqual(jenkins._env_of("xgen Dev (177)"), "dev")
        self.assertEqual(jenkins._env_of("xgen-stage(244)"), "stg")
        self.assertEqual(jenkins._env_of("xgen Prd (244)"), "prd")
        self.assertEqual(jenkins._env_of("example-client-dev"), "dev")

    def test_list_jobs_with_mock(self):
        os.environ["XGEN_MAKER_JENKINS_URL"] = "https://j"
        os.environ["XGEN_MAKER_JENKINS_USER"] = "u"
        os.environ["XGEN_MAKER_JENKINS_TOKEN"] = "t"
        payload = {"jobs": [{"name": "xgen Dev (177)", "color": "blue"},
                            {"name": "xgen-stage(244)", "color": "blue"}]}
        with patch.object(jenkins, "_get", return_value=payload):
            jobs = jenkins.list_jobs()
        self.assertEqual(jobs[0]["env"], "dev")
        self.assertEqual(jobs[1]["env"], "stg")


class TestArgoReadonly(unittest.TestCase):
    def setUp(self):
        for k in ("XGEN_MAKER_ARGOCD_URL", "XGEN_MAKER_ARGOCD_TOKEN",
                  "XGEN_MAKER_ARGOCD_PASSWORD"):
            os.environ.pop(k, None)

    def test_unavailable_without_creds(self):
        self.assertFalse(argocd.available())
        self.assertEqual(argocd.list_apps(), [])

    def test_available_with_token(self):
        os.environ["XGEN_MAKER_ARGOCD_URL"] = "https://a"
        os.environ["XGEN_MAKER_ARGOCD_TOKEN"] = "tok"
        self.assertTrue(argocd.available())
        os.environ.pop("XGEN_MAKER_ARGOCD_URL", None)
        os.environ.pop("XGEN_MAKER_ARGOCD_TOKEN", None)


class TestStageUrl(unittest.TestCase):
    def test_default_urls(self):
        os.environ.pop("XGEN_MAKER_URL_STG", None)
        self.assertEqual(stage_url("stg"), "https://stg.example.com")
        self.assertEqual(stage_url("prd"), "https://app.example.com")

    def test_env_override(self):
        os.environ["XGEN_MAKER_URL_STG"] = "https://custom-stg"
        try:
            self.assertEqual(stage_url("stg"), "https://custom-stg")
        finally:
            os.environ.pop("XGEN_MAKER_URL_STG", None)

    def test_ladder_carries_url_and_jenkins(self):
        os.environ.pop("XGEN_MAKER_URL_DEV", None)
        stages = ladder()
        dev = [s for s in stages if s["env"] == "dev"][0]
        self.assertEqual(dev["url"], "https://dev.example.com")
        self.assertEqual(dev["jenkins"], "xgen Dev")


if __name__ == "__main__":
    unittest.main()
