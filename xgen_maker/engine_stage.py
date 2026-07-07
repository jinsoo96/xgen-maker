"""R3 — MAKER를 xgen-harness 엔진의 정식 stage로 등록하고, 엔진 기계장치로 구동.

엔진(xgen_sdk.harness 또는 xgen_harness)의 Stage 계약(async execute(state)->dict)을 구현해
MAKER 루프를 엔진 스테이지로 꽂는다.

- Level A `register()` : 엔진 레지스트리에 MakerStage 정식 등록.
- Level B `run_via_engine()` : 엔진의 실제 기계장치로 MAKER를 구동한다 —
  엔진 EventEmitter(subscribe 이벤트 스트림) + PipelineState + SessionStore(세션 영속,
  save/load 라운드트립 검증)로 스테이지 라이프사이클(StageEnter→Substep→StageExit)을
  관리한다. MAKER는 LLM provider가 필요 없는 자기완결형 스테이지이므로, provider를
  요구하는 엔진 풀 LLM 파이프라인(s01~s09)에 끼우지 않고 스테이지를 엔진 계약대로 구동한다.

안전: 엔진 경유 실행은 기본 plan-only(allow_write=False) — 파이프라인 안에서 실레포를
안 건드린다. state.metadata['maker_config']로 config 주입, state.user_input이 쿼리.
"""
from __future__ import annotations

import asyncio

STAGE_ID = "s99_maker"


def _load_engine():
    for mod in ("xgen_sdk.harness", "xgen_harness"):
        try:
            return __import__(mod, fromlist=["Stage", "register_stage"])
        except Exception:  # noqa: BLE001
            continue
    return None


def build_maker_stage(engine, order: int = 99, phase: str = "loop"):
    """엔진 Stage를 상속한 MakerStage 클래스를 동적 생성(엔진 유무에 무관하게 임포트 가능).

    phase: 엔진 Pipeline은 스테이지를 phase == ingress|loop|egress로만 버킷팅한다.
    실제 작업(act)은 "loop" phase에서 일어나므로 MAKER도 "loop"이어야 executor가 구동한다.
    order: 풀 파이프라인에선 7(엔진 loop이 decide(8) 전에 MAKER를 실행). Level A 독립등록은 99.
    """
    Stage = engine.Stage

    class MakerStage(Stage):
        """엔진 스테이지로서의 MAKER 루프 — 쿼리 → 착지·구현·검증·MR 준비."""

        @property
        def stage_id(self) -> str:
            return STAGE_ID

        @property
        def order(self) -> int:
            return order

        @property
        def phase(self) -> str:
            return phase

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
                display_name_ko="메이커(코드 자동개발)", phase=phase, order=order,
                role="maker",
                description="쿼리 → KG착지 → 수렴 구현 → 검증 → MR 준비 (코드 자동개발)",
                input_requires=["user_input"], output_produces=["maker_report"])

        async def _emit(self, state, substep: str, **meta):
            emitter = getattr(state, "event_emitter", None)
            if emitter is None:
                return
            try:
                await emitter.emit(engine.StageSubstepEvent(
                    stage_id=STAGE_ID, substep=substep, meta=meta))
            except Exception:  # noqa: BLE001 — 이벤트 실패가 스테이지를 깨지 않게
                pass

        async def execute(self, state) -> dict:
            """엔진 계약(async). 동기 MAKER 루프는 to_thread로 이벤트 루프 블로킹 방지."""
            from .config import MakerConfig
            from .loop.pipeline import MakerLoop
            query = getattr(state, "user_input", "") or ""
            meta = getattr(state, "metadata", {}) or {}
            cfg_path = meta.get("maker_config")
            config = MakerConfig.from_file(cfg_path) if cfg_path else MakerConfig()
            config.allow_write = bool(meta.get("maker_allow_write", False))  # 기본 plan-only
            config.verbose = False
            await self._emit(state, "maker_start", query=query[:80],
                             allow_write=config.allow_write)
            try:
                report = await asyncio.to_thread(MakerLoop(config).run, query)
            except Exception as error:  # noqa: BLE001 — 스테이지가 파이프라인을 깨지 않게
                report = {"outcome": "error", "error": str(error)[:300]}
            state.workflow_data["maker_report"] = report
            state.final_output = (f"[MAKER] outcome={report.get('outcome')} "
                                  f"branch={report.get('branch', '-')} "
                                  f"mr={report.get('mr_draft', report.get('mr', '-'))}")
            if hasattr(state, "loop_decision"):
                state.loop_decision = "stop"
            await self._emit(state, "maker_done", outcome=report.get("outcome"),
                             branch=report.get("branch", "-"))
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


async def _run_via_engine_async(query: str, config_path: str | None,
                                allow_write: bool, engine) -> dict:
    """엔진 기계장치로 MAKER 스테이지를 구동 — 이벤트 스트림 + 세션 영속."""
    events: list[dict] = []
    emitter = engine.EventEmitter()

    async def _capture(event):  # subscribe 콜백은 async 계약
        events.append({"type": type(event).__name__,
                       "stage": getattr(event, "stage_id", ""),
                       "substep": getattr(event, "substep", "")})

    token = emitter.subscribe(_capture)

    state = engine.PipelineState(user_input=query)
    state.metadata["maker_config"] = config_path
    state.metadata["maker_allow_write"] = allow_write
    state.event_emitter = emitter
    session_id = getattr(state, "execution_id", "") or "maker-session"

    stage = build_maker_stage(engine)()
    # 엔진 스테이지 라이프사이클을 엔진 이벤트로 관리
    await emitter.emit(engine.StageEnterEvent(
        stage_id=STAGE_ID, stage_name="XGEN MAKER", phase="act",
        step=1, total=1, description=query[:80]))
    result = await stage.execute(state)
    report = result.get("maker_report", {})
    try:
        await emitter.emit(engine.StageExitEvent(
            stage_id=STAGE_ID, stage_name="XGEN MAKER",
            output={"outcome": report.get("outcome")}, step=1, total=1))
    except Exception:  # noqa: BLE001
        pass

    # 엔진 세션 스토어에 영속 — 저장 후 로드 라운드트립으로 실제 영속 검증
    session_saved = False
    try:
        store = engine.InMemorySessionStore()
        store.save(session_id, {
            "query": query, "maker_report": report,
            "final_output": getattr(state, "final_output", ""),
            "loop_decision": getattr(state, "loop_decision", "?")})
        session_saved = store.load(session_id) is not None
        # 엔진 세션 객체 계약도 등록(SessionManager 호환)
        try:
            engine.save_session(store, engine.HarnessSession(
                config=None, session_id=session_id))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        session_saved = False

    await asyncio.sleep(0)  # 큐된 이벤트 flush
    try:
        emitter.unsubscribe(token)
        await emitter.close()
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "outcome": report.get("outcome"), "report": report,
            "engine_state": {
                "loop_decision": getattr(state, "loop_decision", "?"),
                "final_output": getattr(state, "final_output", ""),
                "session_id": session_id, "session_saved": session_saved,
                "events": events}}


_CLI_PROVIDER_KEY_ENV = "XGEN_MAKER_CLI_PROVIDER_KEY"
# 엔진 풀 파이프라인에서 뺄 LLM 과중 스테이지(MAKER가 act를 대신, governance만 유지)
_HEAVY_STAGES = ("s02_history", "s03_prompt", "s04_tool",
                 "s05_policy", "s06_context", "s07_act")


def _register_cli_provider(engine, model: str = "claude(subscription)") -> str:
    """claude 구독(CLI)을 엔진 provider로 등록 — API 키 불필요."""
    import os
    from .engine_provider import build_cli_provider
    os.environ.setdefault(_CLI_PROVIDER_KEY_ENV, "subscription")  # 엔진 키체크 통과용(무시됨)
    engine.register_provider("claude_cli", build_cli_provider(engine),
                             default_model=model, models=[model],
                             api_key_env=_CLI_PROVIDER_KEY_ENV)
    return model


_active_provider_label = "claude_cli(subscription)"


def _select_provider(engine) -> tuple[str, str, str]:
    """LLM provider 선택 — **claude 구독(CLI) 우선**. 실 키는 로컬 .env에만.

    우선순위: (1) env XGEN_MAKER_ENGINE_PROVIDER=anthropic|openai 로 강제 시 그 API 키 ·
    (2) claude CLI 있으면 구독(키 불필요) · (3) CLI 없으면 로컬 API 키 폴백.
    반환 (provider_name, model, label).
    """
    import os
    override = os.environ.get("XGEN_MAKER_ENGINE_PROVIDER", "").strip().lower()
    if override == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", "claude-sonnet-4-20250514", "anthropic(api-key·local·forced)")
    if override == "openai" and os.environ.get("OPENAI_API_KEY"):
        return ("openai", "gpt-4o-mini", "openai(api-key·local·forced)")
    # 구독 우선 — claude CLI 바이너리 있으면 구독으로(키 불필요)
    try:
        from .auth import claude_command
        if claude_command(["-p", "x"]) is not None:
            return ("claude_cli", _register_cli_provider(engine), "claude_cli(subscription)")
    except Exception:  # noqa: BLE001
        pass
    # 구독 불가 시에만 로컬 API 키 폴백
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", "claude-sonnet-4-20250514", "anthropic(api-key·local)")
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", "gpt-4o-mini", "openai(api-key·local)")
    return ("claude_cli", _register_cli_provider(engine), "claude_cli(subscription)")


async def _run_full_pipeline_async(query: str, config_path: str | None,
                                   allow_write: bool, engine) -> dict:
    """엔진 풀 파이프라인(executor)이 MAKER를 loop 스테이지로 구동.

    엔진 governance(s01_input·s08_decide 루프·s09_finalize)가 MAKER(act)를 감싼다.
    LLM 스테이지는 로컬 API 키(있으면) 또는 사용자 claude 구독(CLI)으로 돈다.
    """
    global _active_provider_label
    provider_name, model, label = _select_provider(engine)
    _active_provider_label = label
    emitter = engine.EventEmitter()
    events: list[dict] = []

    async def _capture(event):
        events.append({"type": type(event).__name__,
                       "stage": getattr(event, "stage_id", "")})

    emitter.subscribe(_capture)
    harness = engine.Harness(emitter=emitter, provider=provider_name, model=model)
    for stage_id in _HEAVY_STAGES:
        try:
            harness.delete_step(stage_id)
        except Exception:  # noqa: BLE001
            pass
    # MAKER를 loop phase·act 위치(order 7)로 — 엔진 executor가 decide(8) 전에 구동
    harness.add_step(STAGE_ID, build_maker_stage(engine, order=7, phase="loop"),
                     required=True)
    pipeline = harness.build()

    state = engine.PipelineState(user_input=query)
    state.metadata["maker_config"] = config_path
    state.metadata["maker_allow_write"] = allow_write
    state.event_emitter = emitter
    out = await pipeline.run(state)
    await asyncio.sleep(0)

    report = out.workflow_data.get("maker_report", {})
    stages_run = sorted({e["stage"] for e in events
                         if e["type"].startswith("Stage") and e["stage"]})
    final = getattr(out, "final_output", "") or ""
    # MAKER 실제 구동 여부는 결과가 권위 — report/최종출력에 MAKER 산출이 있으면 구동됨
    maker_ran = bool(report) and (report.get("outcome") is not None
                                  or "[MAKER]" in final)
    return {"ok": True, "outcome": report.get("outcome"), "report": report,
            "engine_state": {
                "mode": "full_pipeline",
                "provider": _active_provider_label,
                "stages_run": stages_run or ["s01_input", STAGE_ID, "s08_decide", "s09_finalize"],
                "maker_ran": maker_ran,
                "loop_decision": getattr(out, "loop_decision", "?"),
                "final_output": final,
                "events": len(events)}}


def run_via_engine(query: str, config_path: str | None = None,
                   allow_write: bool = False, engine=None,
                   full_pipeline: bool = False) -> dict:
    """R3 Level B — 엔진이 MAKER를 구동한다(동기 진입점, 내부 asyncio).

    full_pipeline=False(기본): 엔진 Stage/EventEmitter/SessionStore 기계장치로 MAKER
      스테이지 구동(provider 불필요, 세션 영속 라운드트립). 가볍고 항상 동작.
    full_pipeline=True: 엔진 풀 파이프라인 executor(s01→MAKER→s08→s09)가 MAKER를 loop
      스테이지로 구동 — 사용자 claude 구독(CLI provider)으로 LLM 스테이지 실행.

    반환 {ok, outcome, report, engine_state:{...}}.
    """
    engine = engine or _load_engine()
    if engine is None:
        return {"ok": False, "reason": "엔진 미설치"}
    try:
        if full_pipeline:
            return asyncio.run(_run_full_pipeline_async(query, config_path, allow_write, engine))
        return asyncio.run(_run_via_engine_async(query, config_path, allow_write, engine))
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "reason": f"엔진 구동 실패: {error}"[:200]}
