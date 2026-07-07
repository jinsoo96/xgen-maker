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
    def test_provider_autoselect(self):
        import os
        from xgen_maker.engine_stage import _select_provider
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
        try:
            # 키 없으면 claude 구독(CLI)
            name, model, label = _select_provider(ENGINE)
            self.assertEqual(name, "claude_cli")
            self.assertIn("subscription", label)
            # 로컬 API 키 있으면 그걸(직접 API)
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-local-xxxx"
            name2, _, label2 = _select_provider(ENGINE)
            self.assertEqual(name2, "anthropic")
            self.assertIn("local", label2)
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
