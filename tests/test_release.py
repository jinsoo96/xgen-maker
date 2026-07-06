import unittest

from xgen_maker.config import MakerConfig
from xgen_maker.kg.graph import Graph
from xgen_maker.loop.release import (ladder, env_for_branch, promotion_path,
                                     release_view, render_ladder_md,
                                     deploy_targets_by_env)


def infra_graph() -> Graph:
    g = Graph()
    g.add_node("xgen-core", "repo", "xgen-core", "xgen-core", "/p")
    g.add_node("xi:app:xgen-core", "helm_app", "xgen-core", "xgen-infra", "")
    g.add_node("xi:project:xgen", "deploy_project", "xgen", "xgen-infra", "",
               namespace="xgen", domains={"dev": "app.example.com", "prd": "prd.example.com"})
    g.add_edge("xi:project:xgen", "xi:app:xgen-core", "deploys",
               envs=["dev", "prd"],
               domains={"dev": "app.example.com", "prd": "prd.example.com"})
    return g


class TestLadder(unittest.TestCase):
    def test_default_ladder_order(self):
        branches = [s["branch"] for s in ladder()]
        self.assertEqual(branches, ["develop", "stg", "main"])
        self.assertEqual([s["env"] for s in ladder()], ["dev", "stg", "prd"])

    def test_env_for_branch(self):
        self.assertEqual(env_for_branch("develop"), "dev")
        self.assertEqual(env_for_branch("main"), "prd")
        self.assertIsNone(env_for_branch("fix/x"))

    def test_promotion_path(self):
        self.assertEqual([s["branch"] for s in promotion_path("develop")],
                         ["develop", "stg", "main"])
        self.assertEqual([s["branch"] for s in promotion_path("stg")], ["stg", "main"])
        self.assertEqual([s["branch"] for s in promotion_path("main")], ["main"])

    def test_deploy_targets_by_env(self):
        g = infra_graph()
        by_env = deploy_targets_by_env(g, "xgen-core")
        self.assertIn("dev", by_env)
        self.assertIn("prd", by_env)
        self.assertEqual(by_env["dev"][0]["domain"], "app.example.com")

    def test_release_view_and_render(self):
        g = infra_graph()
        v = release_view(g, "xgen-core", "develop", MakerConfig())
        self.assertEqual(v["lands_on_env"], "dev")
        self.assertEqual(v["promotion_remaining"], ["develop", "stg", "main"])
        current = [s for s in v["ladder"] if s["current"]]
        self.assertEqual(current[0]["branch"], "develop")
        md = render_ladder_md(v)
        self.assertIn("develop", md)
        self.assertIn("main 직접 머지 금지", md)
        self.assertIn("app.example.com", md)

    def test_custom_stages(self):
        cfg = MakerConfig(release_stages=[{"branch": "develop", "env": "dev", "role": "x"},
                                          {"branch": "main", "env": "prd", "role": "y"}])
        self.assertEqual([s["branch"] for s in ladder(cfg)], ["develop", "main"])


if __name__ == "__main__":
    unittest.main()
