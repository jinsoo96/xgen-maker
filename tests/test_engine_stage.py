import tempfile
import unittest
from pathlib import Path

from xgen_maker.engine_stage import register, build_maker_stage, _load_engine, STAGE_ID

ENGINE = _load_engine()


class TestEngineStage(unittest.TestCase):
    def test_register_or_graceful(self):
        r = register()
        if ENGINE is None:
            self.assertFalse(r["ok"])
            self.assertIn("미설치", r["reason"])
        else:
            self.assertTrue(r["ok"])
            self.assertEqual(r["stage_id"], STAGE_ID)

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_stage_contract(self):
        Stage = build_maker_stage(ENGINE)
        st = Stage()
        self.assertEqual(st.stage_id, STAGE_ID)
        self.assertEqual(st.phase, "loop")  # 엔진 Pipeline이 실행하는 phase(ingress/loop/egress)
        self.assertEqual(st.role, "maker")
        # 풀 파이프라인용은 act 위치(order 7)
        self.assertEqual(build_maker_stage(ENGINE, order=7, phase="loop")().order, 7)
        desc = st.describe()
        self.assertEqual(desc.stage_id, STAGE_ID)
        self.assertIn("maker_report", desc.output_produces)

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_execute_via_pipeline_state(self):
        Stage = build_maker_stage(ENGINE)
        st = Stage()
        # KG 없이도 안전하게 — 질문 intent(레포 미접촉). 임시 빈 KG 사용.
        with tempfile.TemporaryDirectory() as tmp:
            from xgen_maker.kg.graph import Graph
            g = Graph()
            g.add_node("r:a.py", "file", "a.py", "r", "a.py")
            kg = Path(tmp) / "kg.json"
            g.save(kg)
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "worklogs_dir": "{Path(tmp).as_posix()}/wl", '
                           f'"llm_enabled": false, "verbose": false}}', encoding="utf-8")
            state = ENGINE.PipelineState(user_input="a.py 파일 어디 있어?")
            state.metadata["maker_config"] = str(cfg)
            import asyncio
            out = asyncio.run(st.execute(state))  # 엔진 계약 = async
        self.assertIn("maker_report", out)
        self.assertIn("[MAKER]", state.final_output)
        self.assertEqual(state.loop_decision, "stop")


class TestFullPipelineWiring(unittest.TestCase):
    """엔진 풀 파이프라인 구동 배선 — provider 선택·스테이지 phase(LLM 호출 없이)."""

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_provider_autoselect_subscription_first(self):
        import os
        from unittest.mock import patch
        from xgen_maker.engine_stage import _select_provider
        saved = {k: os.environ.pop(k, None) for k in
                 ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XGEN_MAKER_ENGINE_PROVIDER")}
        try:
            # 구독 우선 — CLI 있으면 키가 있어도 구독
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-local-xxxx"
            with patch("xgen_maker.auth.claude_command", return_value=["claude", "-p", "x"]):
                name, _, label = _select_provider(ENGINE)
            self.assertEqual(name, "claude_cli")
            self.assertIn("subscription", label)
            # override로 API 키 강제
            os.environ["XGEN_MAKER_ENGINE_PROVIDER"] = "anthropic"
            with patch("xgen_maker.auth.claude_command", return_value=["claude", "-p", "x"]):
                name2, _, label2 = _select_provider(ENGINE)
            self.assertEqual(name2, "anthropic")
            self.assertIn("forced", label2)
            # CLI 없으면 로컬 키 폴백
            os.environ.pop("XGEN_MAKER_ENGINE_PROVIDER", None)
            with patch("xgen_maker.auth.claude_command", return_value=None):
                name3, _, _ = _select_provider(ENGINE)
            self.assertEqual(name3, "anthropic")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_maker_stage_loop_phase_for_pipeline(self):
        # 엔진 Pipeline은 phase==loop만 실행 — MAKER가 loop이어야 구동됨
        st = build_maker_stage(ENGINE, order=7, phase="loop")()
        self.assertEqual(st.phase, "loop")
        self.assertEqual(st.order, 7)

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_cli_provider_implements_interface(self):
        from xgen_maker.engine_provider import build_cli_provider
        cls = build_cli_provider(ENGINE)
        prov = cls(model="claude(subscription)")
        self.assertEqual(prov.provider_name, "claude_cli")
        self.assertFalse(prov.supports_tool_use())
        # create_provider(name, api_key, model, base_url) 시그니처 수용
        cls("sk-x", "m", None)


if __name__ == "__main__":
    unittest.main()


class TestEngineFullPipelineIntegration(unittest.TestCase):
    """옵트인 통합 — 실 엔진 풀 파이프라인이 구독으로 MAKER 구동(실 LLM 호출, 느림).

    XGEN_MAKER_TEST_FULL_PIPELINE=1 일 때만 실행."""

    @unittest.skipUnless(
        ENGINE is not None and __import__("os").environ.get("XGEN_MAKER_TEST_FULL_PIPELINE") == "1",
        "옵트인(XGEN_MAKER_TEST_FULL_PIPELINE=1)")
    def test_full_pipeline_drives_maker(self):
        import tempfile, json
        from pathlib import Path
        from xgen_maker.kg.graph import Graph
        from xgen_maker.engine_stage import run_via_engine
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph(); g.add_node("r:a.py", "file", "a.py", "r", "a.py")
            kg = Path(tmp) / "kg.json"; g.save(kg)
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"kg_path": kg.as_posix(),
                                       "worklogs_dir": f"{Path(tmp).as_posix()}/wl",
                                       "llm_enabled": False, "verbose": False}), encoding="utf-8")
            r = run_via_engine("a.py 어디 있어?", str(cfg), full_pipeline=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["engine_state"]["mode"], "full_pipeline")
        self.assertTrue(r["engine_state"]["maker_ran"])


class TestEngineRunLevelB(unittest.TestCase):
    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_run_via_engine(self):
        import tempfile, json
        from pathlib import Path
        from xgen_maker.kg.graph import Graph
        from xgen_maker.engine_stage import run_via_engine
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph(); g.add_node("r:a.py", "file", "a.py", "r", "a.py")
            kg = Path(tmp) / "kg.json"; g.save(kg)
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"kg_path": kg.as_posix(),
                                       "worklogs_dir": f"{Path(tmp).as_posix()}/wl",
                                       "llm_enabled": False, "verbose": False,
                                       "fetch_latest": False}), encoding="utf-8")
            r = run_via_engine("a.py 어디 있어?", str(cfg))
        self.assertTrue(r["ok"])
        self.assertEqual(r["outcome"], "answered")
        es = r["engine_state"]
        self.assertEqual(es["loop_decision"], "stop")
        self.assertIn("[MAKER]", es["final_output"])
        # 완전동작: 엔진 세션 영속 라운드트립 + 엔진 이벤트 스트림
        self.assertTrue(es["session_saved"], "세션이 실제로 save→load 라운드트립돼야 함")
        self.assertTrue(es["session_id"])
        etypes = [e["type"] for e in es["events"]]
        self.assertIn("StageEnterEvent", etypes)
        self.assertIn("StageExitEvent", etypes)
        substeps = [e["substep"] for e in es["events"] if e["substep"]]
        self.assertIn("maker_start", substeps)
        self.assertIn("maker_done", substeps)


class TestEngineWebPath(unittest.TestCase):
    """웹 실행도 하네스(엔진) 경유 — 주입 저널로 실시간 스트리밍, 공유 그래프 재사용,
    협조적 취소가 실패가 아닌 '중지'로 전파되는지 고정."""

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_injected_journal_streams_and_reuses_graph(self):
        import json
        from xgen_maker.config import MakerConfig
        from xgen_maker.kg.graph import Graph
        from xgen_maker.loop.journal import Journal
        from xgen_maker.engine_stage import run_via_engine
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph(); g.add_node("r:a.py", "file", "a.py", "r", "a.py")
            kg = Path(tmp) / "kg.json"; g.save(kg)
            cfg = MakerConfig(kg_path=kg.as_posix(),
                              worklogs_dir=f"{Path(tmp).as_posix()}/wl",
                              llm_enabled=False, verbose=False, fetch_latest=False)
            streamed = []

            class _Spy:
                def __init__(self, real): self._real = real; self.dir = real.dir; self.slug = real.slug
                def event(self, step, status, **data):
                    streamed.append(step); return self._real.event(step, status, **data)
                def close(self, outcome): return self._real.close(outcome)

            def factory(worklogs_dir, qtext, verbose=False):
                return _Spy(Journal(worklogs_dir, qtext, verbose=False))

            # 공유 그래프 객체를 그대로 주입 — 엔진이 디스크에서 다시 읽지 않는다
            r = run_via_engine("a.py 어디 있어?", allow_write=False,
                               journal_factory=factory, graph=g, config_obj=cfg)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["outcome"], "answered")
        # 주입 저널이 실제로 단계별로 흘렀다(엔진 경유인데도 스트리밍 유지)
        self.assertIn("intent", streamed)
        self.assertIn("answer", streamed)

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_code_change_runs_full_write_pipeline_via_engine(self):
        """핵심 회귀 방지: 코드 변경 요청이 하네스(엔진) 경유로도 전 쓰기 파이프라인을
        탄다 — branch→implement→checks→commit→MR초안까지. 질문형만 되는 게 아니다."""
        import subprocess, sys
        from xgen_maker.config import MakerConfig
        from xgen_maker.kg.build import build_repo
        from xgen_maker.loop.journal import Journal
        from xgen_maker.engine_stage import run_via_engine

        app_src = 'def greet(name):\n    return "hi " + name\n'
        stub = ('import pathlib\n'
                'p = pathlib.Path("app.py"); s = p.read_text(encoding="utf-8")\n'
                's = s.replace(\'return "hi " + name\', \'return "hi, " + str(name)\')\n'
                'p.write_text(s, encoding="utf-8"); print("patched")\n')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); repo = base / "demo"; repo.mkdir()
            for a in (["init", "-b", "trunk"],
                      ["config", "user.email", "m@t.local"],
                      ["config", "user.name", "m"]):
                subprocess.run(["git", *a], cwd=repo, capture_output=True, check=True)
            (repo / "app.py").write_text(app_src, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

            g = build_repo("demo", repo)
            kg = base / "kg.json"; g.save(kg)
            stub_path = base / "stub.py"; stub_path.write_text(stub, encoding="utf-8")
            cfg = MakerConfig(repos={"demo": str(repo)}, kg_path=str(kg),
                              mode="observe", allow_write=True, llm_enabled=False,
                              verbose=False, fetch_latest=False,
                              agent_cmd=f'"{sys.executable}" "{stub_path}"',
                              worklogs_dir=str(base / "wl"))
            streamed = []

            class _Spy:
                def __init__(self, real): self._real = real; self.dir = real.dir; self.slug = real.slug
                def event(self, step, status, **d):
                    streamed.append(step); return self._real.event(step, status, **d)
                def close(self, o): return self._real.close(o)

            def factory(wd, qt, verbose=False):
                return _Spy(Journal(wd, qt, verbose=False))

            r = run_via_engine("greet 함수가 이름 처리에서 에러 나는 버그 고쳐줘",
                               allow_write=True, mode="observe",
                               journal_factory=factory, graph=g, config_obj=cfg)
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                    cwd=repo, capture_output=True, text=True).stdout.strip()
            report = r.get("report", {})
            # 파일 존재는 임시디렉토리 정리 전에 확인해야 한다
            mr_exists = bool(report.get("session_dir")) and \
                Path(report["session_dir"], "MR-DRAFT.md").exists()
        self.assertTrue(r["ok"], r)
        # 질문이 아니라 실제 커밋까지 갔다(로컬), 브랜치는 fix/, MR 초안 생성
        self.assertEqual(report["outcome"], "committed_local", report)
        self.assertTrue(report["branch"].startswith("fix/"), report)
        self.assertEqual(branch, report["branch"])
        self.assertTrue(mr_exists, "MR 초안이 세션 디렉토리에 생성돼야 함")
        # 엔진 경유인데도 쓰기 단계들이 실제로 흘렀다
        for step in ("branch", "implement", "checks", "commit", "mr_ready"):
            self.assertIn(step, streamed, f"{step} 단계가 엔진 경유 스트림에 없음")

    @unittest.skipUnless(ENGINE is not None, "엔진 미설치")
    def test_cooperative_cancel_reports_cancelled(self):
        import threading
        from xgen_maker.config import MakerConfig
        from xgen_maker.kg.graph import Graph
        from xgen_maker.loop.journal import Journal
        from xgen_maker.engine_stage import run_via_engine

        class _Cancelled(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            g = Graph(); g.add_node("r:a.py", "file", "a.py", "r", "a.py")
            kg = Path(tmp) / "kg.json"; g.save(kg)
            cfg = MakerConfig(kg_path=kg.as_posix(),
                              worklogs_dir=f"{Path(tmp).as_posix()}/wl",
                              llm_enabled=False, verbose=False, fetch_latest=False)
            flag = threading.Event(); flag.set()

            class _Cancelling:
                def __init__(self, real): self._real = real; self.dir = real.dir; self.slug = real.slug
                def event(self, step, status, **data):
                    if flag.is_set():
                        raise _Cancelled()
                    return self._real.event(step, status, **data)
                def close(self, outcome): return self._real.close(outcome)

            def factory(worklogs_dir, qtext, verbose=False):
                return _Cancelling(Journal(worklogs_dir, qtext, verbose=False))

            r = run_via_engine("a.py 어디 있어?", journal_factory=factory,
                               graph=g, config_obj=cfg)
        # 취소는 실패가 아니라 정상 중지 — cancelled 플래그로 구분되어야 한다
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("cancelled"), r)
