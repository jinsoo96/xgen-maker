import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.loop.deploy import deploy_render_test, app_for_repo, _find_helm

HELM = _find_helm()


class TestRepoAppMapping(unittest.TestCase):
    def test_mapping(self):
        # config.deploy_app_map 주입 시 그 매핑, 미설정 시 레포명=앱명(identity)
        cfg = MakerConfig(deploy_app_map={"svc-frontend-features": "svc-frontend",
                                          "svc-frontend-app": "svc-frontend"})
        self.assertEqual(app_for_repo("svc-frontend-features", cfg), "svc-frontend")
        self.assertEqual(app_for_repo("svc-frontend-app", cfg), "svc-frontend")
        self.assertEqual(app_for_repo("svc-core", cfg), "svc-core")   # 미매핑 → identity
        self.assertEqual(app_for_repo("svc-core", None), "svc-core")  # config 없음 → identity
        self.assertIsNone(app_for_repo("", None))                     # 빈 레포 → None


class TestDeployRenderGuards(unittest.TestCase):
    def test_skip_empty_repo(self):
        cfg = MakerConfig(infra_path="/nope")
        r = deploy_render_test(cfg, "")
        self.assertEqual(r["status"], "skipped")
        self.assertIn("매핑 없음", r["reason"])

    def test_skip_missing_chart(self):
        cfg = MakerConfig(infra_path="/definitely/not/here")
        r = deploy_render_test(cfg, "svc-core")
        self.assertEqual(r["status"], "skipped")


def _make_chart(root: Path, template: str) -> None:
    chart = root / "k3s" / "helm-chart"
    (chart / "templates").mkdir(parents=True)
    (chart / "values").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: xgen-service\nversion: 0.1.0\n", encoding="utf-8")
    (chart / "templates" / "deployment.yaml").write_text(template, encoding="utf-8")
    (chart / "values" / "svc-core.yaml").write_text("image: myimg:1.0\n", encoding="utf-8")


@unittest.skipUnless(HELM, "helm 미설치")
class TestDeployRenderWithHelm(unittest.TestCase):
    def test_render_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_chart(root, (
                "apiVersion: apps/v1\nkind: Deployment\n"
                "metadata:\n  name: svc-core\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: c\n        image: {{ .Values.image }}\n"))
            cfg = MakerConfig(infra_path=str(root))
            r = deploy_render_test(cfg, "svc-core")
        self.assertEqual(r["status"], "passed")
        self.assertEqual(r["kinds"].get("Deployment"), 1)

    def test_render_fail_broken_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 닫히지 않은 helm 액션 → 템플릿 파싱 실패
            _make_chart(root, "kind: Deployment\nname: {{ .Values.image \n")
            cfg = MakerConfig(infra_path=str(root))
            r = deploy_render_test(cfg, "svc-core")
        self.assertEqual(r["status"], "failed")
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
