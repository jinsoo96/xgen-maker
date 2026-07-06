import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.extract_infra import (extract_infra, link_infra_to_code,
                                         deploy_targets, HAS_YAML)


def make_infra(root: Path) -> None:
    chart = root / "k3s" / "helm-chart"
    (chart / "values").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: xgen-service\nversion: 0.1\n",
                                      encoding="utf-8")
    (chart / "values" / "xgen-core.yaml").write_text(
        "serviceName: xgen-core\n", encoding="utf-8")
    (chart / "values" / "xgen-frontend.yaml").write_text(
        "serviceName: xgen-frontend\n", encoding="utf-8")
    proj = root / "k3s" / "argocd" / "projects"
    proj.mkdir(parents=True)
    (proj / "xgen.yaml").write_text(
        "project:\n  name: xgen\n  namespace: xgen\n"
        "destinations:\n  dev:\n    domain: \"app.example.com\"\n"
        "site: main\n"
        "environments:\n  dev:\n    services:\n"
        "      - name: xgen-frontend\n        hasDomain: true\n"
        "      - name: xgen-core\n", encoding="utf-8")
    (proj / "project-b.yaml").write_text(
        "project:\n  name: project-b\n  namespace: project-b\n"
        "destinations:\n  dev:\n    domain: \"app-a-dev.example.com\"\n"
        "environments:\n  dev:\n    services:\n      - name: xgen-core\n",
        encoding="utf-8")


@unittest.skipUnless(HAS_YAML, "PyYAML 없음")
class TestInfraExtract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_infra(self.root)
        self.g = extract_infra(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nodes(self):
        self.assertEqual(len(self.g.nodes_by_kind("deploy_project")), 2)
        self.assertEqual(len(self.g.nodes_by_kind("helm_app")), 2)
        self.assertEqual(len(self.g.nodes_by_kind("helm_chart")), 1)

    def test_deploy_edges_and_domain(self):
        proj = [n for n in self.g.nodes_by_kind("deploy_project") if n["name"] == "xgen"][0]
        self.assertEqual(proj["meta"]["domains"]["dev"], "app.example.com")
        deploys = [e for e in self.g.edges if e["kind"] == "deploys"]
        self.assertTrue(deploys)
        serves = [e for e in self.g.edges if e["kind"] == "serves"]
        self.assertTrue(any("app.example.com" in (e["meta"].get("domains", {}) or {}).values()
                            for e in serves))

    def test_link_and_targets(self):
        # 코드 레포 노드 추가 후 연결
        self.g.add_node("xgen-core", "repo", "xgen-core", "xgen-core", "/path")
        self.g.add_node("xgen-frontend-features", "repo", "xgen-frontend-features",
                        "xgen-frontend-features", "/path")
        n = link_infra_to_code(self.g)
        self.assertGreaterEqual(n, 2)
        targets = deploy_targets(self.g, "xgen-core")
        names = {t["project"] for t in targets}
        self.assertEqual(names, {"xgen", "project-b"})
        # frontend alias → xgen-frontend app
        ft = deploy_targets(self.g, "xgen-frontend-features")
        self.assertTrue(any(t["domain"] == "app.example.com" for t in ft))


if __name__ == "__main__":
    unittest.main()
