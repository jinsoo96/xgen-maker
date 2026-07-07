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
        self.assertEqual(st.phase, "act")
        self.assertEqual(st.role, "maker")
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


if __name__ == "__main__":
    unittest.main()


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
