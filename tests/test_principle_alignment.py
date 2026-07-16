"""원칙-대-코드 정합 검수에서 나온 드리프트 수정에 대한 회귀 테스트."""
import unittest

from xgen_maker.config import MakerConfig, resolve_default_repo


class TestNoHardcodedOrgNames(unittest.TestCase):
    """public-safe: 조직 서비스명·인프라 매핑을 소스에 담지 않는다(config 주입)."""

    def test_app_map_is_config_driven(self):
        from xgen_maker.loop.deploy import app_for_repo
        # 소스에 하드코딩 매핑이 없어야 함 → config 없으면 identity
        self.assertEqual(app_for_repo("anything", None), "anything")
        cfg = MakerConfig(deploy_app_map={"frontend-x": "frontend"})
        self.assertEqual(app_for_repo("frontend-x", cfg), "frontend")

    def test_profile_map_is_config_driven(self):
        from xgen_maker.loop.verify import suggest_profiles
        self.assertEqual(suggest_profiles(["a", "b"], None), ["a", "b"])  # identity
        cfg = MakerConfig(stack_profile_map={"a": "prof-a"})
        self.assertEqual(suggest_profiles(["a", "b"], cfg), ["b", "prof-a"])

    def test_no_xgen_repo_names_in_source(self):
        # deploy/verify/extract_infra/cli 소스에 실 xgen-* 서비스명이 없어야 함
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "xgen_maker"
        banned = ("xgen-core", "xgen-workflow", "xgen-frontend", "xgen-documents",
                  "xgen-mcp-station", "xgen-backend-gateway", "xgen-workbench")
        hits = []
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for name in banned:
                if name in text:
                    hits.append(f"{py.name}:{name}")
        self.assertEqual(hits, [], f"소스에 실 서비스명 잔존: {hits}")


class TestDefaultRepoResolution(unittest.TestCase):
    def test_resolve_from_gitlab_projects(self):
        cfg = MakerConfig(gitlab_projects={"beta": "g/beta", "alpha": "g/alpha"})
        self.assertEqual(resolve_default_repo(cfg), "alpha")  # 정렬 첫 키

    def test_explicit_default_wins(self):
        cfg = MakerConfig(default_repo="chosen", gitlab_projects={"a": "g/a"})
        self.assertEqual(resolve_default_repo(cfg), "chosen")

    def test_empty_when_no_repos(self):
        self.assertEqual(resolve_default_repo(MakerConfig()), "")


class TestJudgeHeuristicHonesty(unittest.TestCase):
    def test_source_tagged(self):
        from xgen_maker.loop.judge import judge
        cfg = MakerConfig(llm_enabled=False, theta=0.7)
        r = judge(cfg, "q", "diff --git a/x.py\n+fix\n", ["x.py"])
        self.assertEqual(r["source"], "heuristic")  # LLM 없으면 정직하게 heuristic

    def test_mr_draft_flags_heuristic(self):
        from xgen_maker.loop.mr import build_mr_draft
        _, body = build_mr_draft("q", "bug", "fix/x", "develop", ["x.py"], "diff", [],
                                 {"score": 0.9, "theta": 0.7, "source": "heuristic",
                                  "reasons": []})
        self.assertIn("휴리스틱 판정", body)
        self.assertIn("실제 품질 평가 아님", body)
        # llm 판정이면 경고 없음
        _, body2 = build_mr_draft("q", "bug", "fix/x", "develop", ["x.py"], "diff", [],
                                  {"score": 0.9, "theta": 0.7, "source": "llm", "reasons": []})
        self.assertNotIn("휴리스틱 판정", body2)


if __name__ == "__main__":
    unittest.main()
