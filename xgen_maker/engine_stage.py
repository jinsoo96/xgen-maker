"""R3 — MAKER를 xgen-harness 엔진의 정식 stage로 등록.

엔진(xgen_sdk.harness 또는 xgen_harness)의 Stage 계약(execute(state)->dict)을 구현해,
MAKER 루프를 엔진 Pipeline의 한 스테이지로 꽂는다. register 후엔 엔진이 MAKER를 인지·구동한다.

안전: 엔진 경유 실행은 기본 plan-only(allow_write=False) — 파이프라인 안에서 실레포를 함부로
안 건드린다. state.metadata['maker_config']로 config 주입, state.user_input이 쿼리.

전체 네이티브 이식(엔진 Phase B 루프가 MAKER 수렴을 구동)은 s06/s08 배선이 남은 다음 단계.
이 모듈은 '정식 stage 등록 + 엔진이 MAKER를 스테이지로 실행'까지를 담당한다.
"""
from __future__ import annotations

STAGE_ID = "s99_maker"


def _load_engine():
    for mod in ("xgen_sdk.harness", "xgen_harness"):
        try:
            return __import__(mod, fromlist=["Stage", "register_stage"])
        except Exception:  # noqa: BLE001
            continue
    return None


def build_maker_stage(engine):
    """엔진 Stage를 상속한 MakerStage 클래스를 동적 생성(엔진 유무에 무관하게 임포트 가능)."""
    Stage = engine.Stage

    class MakerStage(Stage):
        """엔진 스테이지로서의 MAKER 루프 — 쿼리 → 착지·구현·검증·MR 준비."""

        @property
        def stage_id(self) -> str:
            return STAGE_ID

        @property
        def order(self) -> int:
            return 99

        @property
        def phase(self) -> str:
            return "act"

        @property
        def role(self) -> str:
            return "maker"

        @property
        def display_name(self) -> str:
            return "XGEN MAKER"

        @property
        def display_name_ko(self) -> str:
            return "메이커(코드 자동개발)"

        def describe(self):
            return engine.StageDescription(
                stage_id=STAGE_ID, display_name="XGEN MAKER",
                display_name_ko="메이커(코드 자동개발)", phase="act", order=99,
                role="maker",
                description="쿼리 → KG착지 → 수렴 구현 → 검증 → MR 준비 (코드 자동개발)",
                input_requires=["user_input"], output_produces=["maker_report"])

        def execute(self, state) -> dict:
            from .config import MakerConfig
            from .loop.pipeline import MakerLoop
            query = getattr(state, "user_input", "") or ""
            meta = getattr(state, "metadata", {}) or {}
            cfg_path = meta.get("maker_config")
            config = MakerConfig.from_file(cfg_path) if cfg_path else MakerConfig()
            config.allow_write = bool(meta.get("maker_allow_write", False))  # 기본 plan-only
            config.verbose = False
            try:
                report = MakerLoop(config).run(query)
            except Exception as error:  # noqa: BLE001 — 스테이지가 파이프라인을 깨지 않게
                report = {"outcome": "error", "error": str(error)[:300]}
            state.workflow_data["maker_report"] = report
            state.final_output = (f"[MAKER] outcome={report.get('outcome')} "
                                  f"branch={report.get('branch','-')} "
                                  f"mr={report.get('mr_draft', report.get('mr','-'))}")
            if hasattr(state, "loop_decision"):
                state.loop_decision = "stop"
            return {"maker_report": report}

    return MakerStage


def register(engine=None) -> dict:
    """엔진에 MAKER 스테이지 등록. 반환 {ok, stage_id, engine}."""
    engine = engine or _load_engine()
    if engine is None:
        return {"ok": False, "reason": "xgen-harness/xgen-sdk 미설치"}
    try:
        stage_cls = build_maker_stage(engine)
        engine.register_stage(STAGE_ID, "default", stage_cls)
        return {"ok": True, "stage_id": STAGE_ID,
                "engine": getattr(engine, "__name__", "?"),
                "version": getattr(engine, "__version__", "?")}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "reason": str(error)[:200]}
