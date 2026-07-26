"""파일 간 호출 링크 — 중심성·체인·영향분석의 근간.

회귀: TS·Rust는 calls 엣지가 0이었다(파이썬만 있었다). 호출 그래프가 비면 그 언어에서
중심성도 "이걸 고치면 누가 깨지나"도 통째로 무력해진다.
"""
import unittest

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.calls import scan_call_names, record_calls, link_calls


class TestScanCallNames(unittest.TestCase):
    def test_finds_calls_skips_keywords(self):
        src = "if (x) { doWork(a); return helper(b); } for (i) loop_it();"
        names = scan_call_names(src)
        self.assertIn("doWork", names)
        self.assertIn("helper", names)
        self.assertIn("loop_it", names)
        self.assertNotIn("if", names)      # 제어문은 호출이 아니다
        self.assertNotIn("for", names)
        self.assertNotIn("return", names)


class TestLinkCalls(unittest.TestCase):
    def _graph(self) -> Graph:
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        # a.ts가 정의하고 부르는 것(같은 파일) + b.ts의 함수를 부르는 것(파일 간)
        g.add_node("r:a.ts", "file", "a.ts", "r", "a.ts")
        g.add_node("r:a.ts#localHelper", "function", "localHelper", "r", "a.ts", 1)
        g.add_node("r:b.ts", "file", "b.ts", "r", "b.ts")
        g.add_node("r:b.ts#sharedUtil", "function", "sharedUtil", "r", "b.ts", 1)
        # 같은 이름이 두 곳(모호) — 링크하면 안 된다
        g.add_node("r:c.ts#ambiguous", "function", "ambiguous", "r", "c.ts", 1)
        g.add_node("r:d.ts#ambiguous", "function", "ambiguous", "r", "d.ts", 1)
        return g

    def test_same_file_and_unique_cross_file_linked_ambiguous_skipped(self):
        g = self._graph()
        # a.ts가 localHelper(같은 파일)·sharedUtil(유일, 파일 간)·ambiguous(모호) 호출
        record_calls(g, "r:a.ts", "function run() { localHelper(); sharedUtil(); ambiguous(); }")
        added = link_calls(g, "r")
        self.assertGreaterEqual(added, 2)
        calls = {(e["dst"], e["meta"].get("role")) for e in g.edges if e["kind"] == "calls"}
        self.assertIn(("r:a.ts#localHelper", "same_file"), calls)
        self.assertIn(("r:b.ts#sharedUtil", "cross_file"), calls)
        # 모호한 이름은 타입 없이 못 고르므로 링크하지 않는다(오탐 방지)
        self.assertNotIn("r:c.ts#ambiguous", {dst for dst, _ in calls})
        self.assertNotIn("r:d.ts#ambiguous", {dst for dst, _ in calls})

    def test_pending_scratch_is_cleared_and_not_serialized(self):
        g = self._graph()
        record_calls(g, "r:a.ts", "function run() { localHelper(); }")
        self.assertIn("_pending_calls", g.__dict__)
        link_calls(g, "r")
        self.assertNotIn("_pending_calls", g.__dict__)   # 소진돼야 한다
        # 빌드 임시 구조는 meta(직렬화 대상)에 새지 않는다
        self.assertNotIn("_pending_calls", g.meta)


class TestIncrementalSyncKeepsInboundCalls(unittest.TestCase):
    """회귀: 증분 동기화가 '들어오는' 호출 엣지를 지워, 동기화할수록 호출 그래프가 닳았다.

    b.py만 바뀌어도 a.py→b.py#func 엣지가 사라졌다 — a.py는 이번에 안 읽으니 복구도
    안 됐다. 중심성·영향분석이 조용히 무너지는 경로였다.
    """

    def _repo(self, tmp):
        import subprocess
        from pathlib import Path
        root = Path(tmp)
        (root / "util.py").write_text("def shared_util():\n    return 1\n", encoding="utf-8")
        (root / "main.py").write_text(
            "from util import shared_util\ndef go():\n    return shared_util()\n",
            encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        return root

    def test_inbound_call_survives_refresh(self):
        import tempfile
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            g = build_repo("r", root)
            inbound = lambda: [e for e in g.edges
                               if e["kind"] == "calls" and e["src"] == "r:main.py"]
            self.assertTrue(inbound(), "빌드 직후 main→util 호출이 있어야 함")
            (root / "util.py").write_text("def shared_util():\n    return 2\n", encoding="utf-8")
            refresh_files(g, "r", root, ["util.py"])
            self.assertTrue(inbound(), "증분 갱신 후에도 들어오는 호출이 살아 있어야 함")

    def test_deleted_symbol_leaves_no_dangling_edge(self):
        import tempfile
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            g = build_repo("r", root)
            # 심볼을 개명 = 옛 심볼 삭제. 그를 가리키던 엣지는 끊겨야 한다.
            (root / "util.py").write_text("def renamed_util():\n    return 3\n", encoding="utf-8")
            refresh_files(g, "r", root, ["util.py"])
            dangling = [e for e in g.edges if e["dst"] not in g.nodes]
            self.assertEqual(dangling, [], "사라진 심볼을 가리키는 엣지가 남으면 안 됨")


class TestRustAndTsProduceCalls(unittest.TestCase):
    """언어 무관하게 호출 그래프가 실제로 생긴다(회귀: TS/Rust가 0이었다)."""

    def test_typescript_file_records_calls(self):
        from xgen_maker.kg.extract_typescript import extract_ts_file
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "u.ts").write_text("export function util() { return 1 }", encoding="utf-8")
            (root / "m.ts").write_text(
                "export function main() { return util() }", encoding="utf-8")
            g = Graph()
            g.add_node("r", "repo", "r", "r", str(root))
            extract_ts_file(g, "r", root, "u.ts", {"u.ts", "m.ts"})
            extract_ts_file(g, "r", root, "m.ts", {"u.ts", "m.ts"})
            link_calls(g, "r")
        calls = [e for e in g.edges if e["kind"] == "calls"]
        self.assertTrue(any(e["dst"] == "r:u.ts#util" for e in calls),
                        "m.ts가 u.ts의 util을 부르는 파일 간 호출이 잡혀야 함")
