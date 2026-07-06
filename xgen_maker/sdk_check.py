"""SDK 자가검증 — MAKER가 의존하는 엔진(xgen-sdk/harness)이 여전히 호환되는지 스스로 확인.

엔진의 introspection 패턴(get_catalog·VerifyResult·snapshot) 차용:
1) 버전 드리프트: 설치 vs PyPI 최신 → 얼마나 뒤졌나.
2) 계약 프로브: MAKER가 쓰는 엔진 심볼/속성이 설치 버전에 여전히 있나(getattr 검증).
   → 엔진이 API를 바꿔도 MAKER가 조용히 깨지지 않고 미리 잡는다.
3) MAKER 자기 카탈로그(honest introspection): MAKER 능력·명령을 기계가 읽을 구조로 자기서술.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

# MAKER가 엔진에서 실제로 쓰는 계약 (converge.py·engine_stage.py 근거)
_ENGINE_SYMBOLS = ["Stage", "StageDescription", "PipelineState",
                   "register_stage", "run_sandboxed", "get_catalog"]
_STAGE_METHODS = ["execute"]
_STATE_FIELDS = ["user_input", "metadata", "final_output", "loop_decision", "workflow_data"]


def installed_versions() -> dict:
    import importlib.metadata as m
    out = {}
    for pkg in ("xgen-sdk", "xgen-harness"):
        try:
            out[pkg] = m.version(pkg)
        except m.PackageNotFoundError:
            out[pkg] = None
    return out


def latest_versions(timeout: int = 12) -> dict:
    out = {}
    for pkg in ("xgen-sdk", "xgen-harness"):
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json",
                                        timeout=timeout) as r:
                out[pkg] = json.loads(r.read().decode("utf-8"))["info"]["version"]
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError, TimeoutError):
            out[pkg] = None
    return out


def _ver_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except (ValueError, AttributeError):
        return (0,)


def _load_engine():
    for mod in ("xgen_sdk.harness", "xgen_harness"):
        try:
            return __import__(mod, fromlist=["Stage"]), mod
        except Exception:  # noqa: BLE001
            continue
    return None, None


def contract_probe() -> dict:
    """설치된 엔진에 MAKER 계약 심볼/속성이 있나. 반환 {ok, engine, present[], missing[]}."""
    engine, name = _load_engine()
    if engine is None:
        return {"ok": False, "engine": None, "present": [],
                "missing": ["<engine 미설치>"]}
    present, missing = [], []
    for sym in _ENGINE_SYMBOLS:
        (present if hasattr(engine, sym) else missing).append(sym)
    # Stage.execute 있나
    stage = getattr(engine, "Stage", None)
    for meth in _STAGE_METHODS:
        label = f"Stage.{meth}"
        (present if stage and hasattr(stage, meth) else missing).append(label)
    # PipelineState 필드 (인스턴스 생성해 확인)
    ps = getattr(engine, "PipelineState", None)
    inst = None
    if ps is not None:
        try:
            inst = ps(user_input="probe")
        except Exception:  # noqa: BLE001
            inst = None
    for field in _STATE_FIELDS:
        label = f"PipelineState.{field}"
        (present if inst is not None and hasattr(inst, field) else missing).append(label)
    # run_sandboxed 실제 동작
    sandbox_ok = False
    if hasattr(engine, "run_sandboxed"):
        try:
            sandbox_ok = engine.run_sandboxed("x=1", timeout_sec=5).exit_code == 0
        except Exception:  # noqa: BLE001
            sandbox_ok = False
    return {"ok": not missing and sandbox_ok, "engine": name,
            "present": present, "missing": missing, "sandbox_ok": sandbox_ok}


def self_check() -> dict:
    """SDK 호환 자가검증 종합. verdict: ok | drift | broken."""
    inst = installed_versions()
    latest = latest_versions()
    probe = contract_probe()
    drift = {}
    for pkg in inst:
        if inst[pkg] and latest.get(pkg):
            behind = _ver_tuple(latest[pkg]) > _ver_tuple(inst[pkg])
            drift[pkg] = {"installed": inst[pkg], "latest": latest[pkg], "behind": behind}
    any_behind = any(d.get("behind") for d in drift.values())
    if not probe["ok"]:
        verdict = "broken"   # 계약 깨짐 — MAKER의 엔진 연동이 안 됨
    elif any_behind:
        verdict = "drift"    # 동작하나 최신 아님 — 업그레이드 시 재검증 필요
    else:
        verdict = "ok"
    return {"verdict": verdict, "drift": drift, "contract": probe}


def maker_catalog() -> dict:
    """MAKER 자기 카탈로그 (honest introspection) — 능력·명령을 기계가 읽는 구조로 자기서술."""
    capabilities = {
        "kg": ["build", "merge", "search", "chain", "impact", "enrich", "domains",
               "tour", "sync", "infra", "annotate", "dashboard"],
        "loop": ["intent", "kg_search", "chain", "legacy_check", "learnings",
                 "converge(sandbox+checks+judge)", "deploy_render", "release", "mr"],
        "verify": ["checks(py_compile/pytest/node)", "sandbox(engine)", "ui(route+pixel+vision)",
                   "authed_ui", "deploy_render(helm)"],
        "observe": ["status(jenkins/argocd)", "mrs", "branches", "history", "learn"],
        "surface": ["cli(run/chat)", "web(dashboard)", "mcp", "engine_stage(s99_maker)"],
        "safety": ["protected_branch_guard", "branch_naming_rule", "infra_veto",
                   "deploy_interlock", "MR-only(배포는 사용자)"],
    }
    return {"name": "xgen-maker", "capabilities": capabilities,
            "boundary": "자동=MR 준비까지 · 배포=사용자 수동 · 관측=read-only"}
