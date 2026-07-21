import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig, infra_files, is_allowed_branch
from xgen_maker.loop.git_ops import GitRepo, GitOpsError
from xgen_maker.loop.intent import classify
from xgen_maker.loop.journal import Journal, slugify
from xgen_maker.loop.judge import judge
from xgen_maker.loop.verify import suggest_profiles


def init_repo(root: Path) -> None:
    for args in (["init", "-b", "trunk"],
                 ["config", "user.email", "maker@test.local"],
                 ["config", "user.name", "maker-test"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    (root / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)


class TestBranchGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        init_repo(self.root)
        self.repo = GitRepo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_protected_branch_rejected(self):
        for name in ("develop", "main", "stg"):
            with self.assertRaises(GitOpsError):
                self.repo.create_branch(name)

    def test_prefixless_branch_rejected(self):
        with self.assertRaises(GitOpsError):
            self.repo.create_branch("hack-something")

    def test_allowed_branch_created(self):
        self.repo.create_branch("fix/test-guard")
        self.assertEqual(self.repo.current_branch(), "fix/test-guard")

    def test_push_protected_rejected(self):
        with self.assertRaises(GitOpsError):
            self.repo.push("develop")

    def test_meaningless_branch_rejected(self):
        # 팀 규칙: js·251205 등 의미 불명 이름 금지
        for name in ("fix/js", "feature/251205", "refactor/x", "fix/"):
            with self.assertRaises(GitOpsError):
                self.repo.create_branch(name)

    def test_hotfix_prefix_allowed(self):
        self.repo.create_branch("hotfix/login-crash")
        self.assertEqual(self.repo.current_branch(), "hotfix/login-crash")

    def test_is_allowed_branch(self):
        self.assertTrue(is_allowed_branch("fix/a"))
        self.assertTrue(is_allowed_branch("feature/b"))
        self.assertFalse(is_allowed_branch("develop"))
        self.assertFalse(is_allowed_branch("wip/a"))


class TestIntent(unittest.TestCase):
    def test_bug(self):
        result = classify("그래프 뷰가 안 돼. 고쳐줘")
        self.assertEqual(result["intent"], "bug")
        self.assertEqual(result["branch_prefix"], "fix/")

    def test_feature(self):
        result = classify("대시보드에 통계 위젯 추가해줘")
        self.assertEqual(result["intent"], "feature")

    def test_question(self):
        result = classify("결제 플로우가 어디에 있어?")
        self.assertEqual(result["intent"], "question")

    def test_change_verb_beats_question_mark(self):
        result = classify("이 에러 왜 나는지 보고 고쳐줄래?")
        self.assertEqual(result["intent"], "bug")

    def test_removal_commands_are_changes_not_questions(self):
        # '지워/삭제' 같은 제거 명령이 question으로 새면 루프가 답만 하고 끝난다
        for q in ("주석 지워줘", "하네스 노드 주석 지워줘", "과한 주석 정리해"):
            self.assertEqual(classify(q)["intent"], "refactor", q)

    def test_descriptive_change_verb_stays_question(self):
        # 회귀 방지: 서술형 '변경/수정'이 질문을 가로채면 MAKER가 답 대신 브랜치를 만들어
        # 코드를 고치려 든다(observe/act에선 실제 변경). 질문 신호가 더 강하면 질문이어야 함.
        for q in ("이 설정 어디서 변경되는지 알려줘",
                  "로그인 로직 어디서 수정되는지 설명해줘",
                  "배포 설정 뭐가 바뀌었는지 알려줘"):
            r = classify(q)
            self.assertEqual(r["intent"], "question", f"{q} → {r['scores']}")
            self.assertEqual(r["branch_prefix"], "")  # 브랜치를 만들지 않는다


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.config = MakerConfig(llm_enabled=False, theta=0.7)

    def test_empty_diff_veto(self):
        result = judge(self.config, "q", "", [])
        self.assertFalse(result["passed"])
        self.assertIn("빈 diff", result["veto"])

    def test_infra_veto(self):
        result = judge(self.config, "q", "diff --git a/docker-compose.yml",
                       ["docker-compose.yml", "src/app.py"])
        self.assertFalse(result["passed"])
        self.assertIn("인프라", result["veto"])

    def test_small_focused_change_passes(self):
        result = judge(self.config, "q", "diff --git a/src/app.py\n+fix\n",
                       ["src/app.py"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["source"], "heuristic")

    def test_infra_patterns(self):
        touched = infra_files(["Dockerfile", "src/a.py", "infra/x.tf",
                               ".gitlab-ci.yml", "docs/Dockerfile.md"])
        self.assertIn("Dockerfile", touched)
        self.assertIn("infra/x.tf", touched)
        self.assertIn(".gitlab-ci.yml", touched)
        self.assertNotIn("src/a.py", touched)

    def test_infra_patterns_ci_descriptors(self):
        # CI-as-code(Jenkins·drone·azure 등)도 인프라 veto 대상
        touched = infra_files(["Jenkinsfile", "ci/Jenkinsfile.release",
                               ".drone.yml", "azure-pipelines.yml",
                               ".circleci/config.yml", "src/ok.py"])
        for f in ("Jenkinsfile", "ci/Jenkinsfile.release", ".drone.yml",
                  "azure-pipelines.yml", ".circleci/config.yml"):
            self.assertIn(f, touched)
        self.assertNotIn("src/ok.py", touched)


class TestJournalAndVerify(unittest.TestCase):
    def test_journal_events_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(tmp, "테스트 쿼리 fix something")
            journal.event("step1", "ok", detail="x")
            summary = journal.close("done")
            self.assertTrue(summary.exists())
            lines = (journal.dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)  # start + step1 + end

    def test_slugify_korean_fallback(self):
        slug = slugify("그래프 뷰 갱신 버그")
        self.assertTrue(slug.startswith("task-"))
        self.assertEqual(slugify("fix graph refresh"), "fix-graph-refresh")

    def test_suggest_profiles(self):
        # config.stack_profile_map 주입 시 매핑, 미설정 시 레포명 identity
        cfg = MakerConfig(stack_profile_map={"svc-frontend": "frontend", "svc-core": "core"})
        self.assertEqual(suggest_profiles(["svc-frontend", "svc-core"], cfg),
                         ["core", "frontend"])
        self.assertEqual(suggest_profiles(["svc-frontend", "svc-core"], None),
                         ["svc-core", "svc-frontend"])  # identity

class TestDependentsReachAgent(unittest.TestCase):
    """9회차 검수(로직 추적): 회귀는 '나를 쓰는 쪽'에서 나는데, 에이전트 프롬프트엔
    정방향 체인만 들어가 의존자를 몰랐다. impact(역방향)를 프롬프트에 넣는다."""

    def test_chain_is_forward_impact_is_reverse(self):
        # 두 검색의 방향이 실제로 다르다는 사실을 고정(뒤바뀌면 회귀방지가 무력화)
        import tempfile
        from pathlib import Path
        from xgen_maker.kg.build import build_repo
        from xgen_maker.kg.search import retrieve_chain, impact
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t) / "demo"
            (repo / "service").mkdir(parents=True)
            (repo / "service" / "pay.py").write_text(
                '"""결제."""\ndef charge(user, amount):\n    return amount\n', encoding="utf-8")
            (repo / "service" / "report.py").write_text(
                '"""리포트."""\nfrom service.pay import charge\n'
                'def monthly(u):\n    return charge(u, 100)\n', encoding="utf-8")
            g = build_repo("demo", repo)
            seed = "demo:service/pay.py#charge"
            chain = [c["name"] for c in retrieve_chain(g, "charge 결제", k=6, hops=2)["chain"]]
            deps = [n["path"] for n in impact(g, seed, depth=3)]
            self.assertNotIn("service/report.py", " ".join(chain),
                             "chain은 정방향이라 의존자를 못 찾는다(설계 사실)")
            self.assertIn("service/report.py", deps,
                          "impact는 역방향이라 의존자를 찾아야 한다")

    def test_prompt_includes_dependents_block(self):
        from xgen_maker.loop.implement import build_prompt
        landing = [{"kind": "function", "name": "charge", "repo": "demo",
                    "path": "service/pay.py", "line": 2}]
        deps = [{"kind": "file", "name": "report.py", "repo": "demo",
                 "path": "service/report.py", "distance": 2}]
        p = build_prompt("charge 고쳐줘", "refactor", landing, "", dependents=deps)
        self.assertIn("이 코드를 쓰는 곳", p)
        self.assertIn("service/report.py", p)
        # repo 컨테이너 노드는 노이즈라 제외
        deps2 = deps + [{"kind": "repo", "name": "demo", "repo": "demo", "path": "/x"}]
        self.assertNotIn("[repo]", build_prompt("q", "refactor", landing, "", dependents=deps2))
        # 의존자가 없으면 블록 자체가 안 나온다
        self.assertNotIn("이 코드를 쓰는 곳", build_prompt("q", "refactor", landing, ""))


if __name__ == "__main__":
    unittest.main()
