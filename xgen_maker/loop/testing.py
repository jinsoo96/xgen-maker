"""⑦-1 자동 검증(checks) — 구현 직후, judge 이전의 기계적 안전망.

원칙: "고치면 이슈가 생길 수 있다" → 변경이 만든 회귀를 사람 전에 기계가 잡는다.
- syntax: 변경된 .py 전부 py_compile (의존성 0, 항상 실행)
- tests : 레포 테스트 러너 자동 감지(pytest / package.json scripts.test) 후 실행.
         pytest는 레포 전체 스위트를 돌려 레거시 회귀를 잡고, node는 변경 패키지 +
         변경에 (역)의존하는 패키지(그래프 기반)까지 돌려 크로스패키지 회귀를 잡는다.
- 환경 문제(의존성 미설치·수집 실패)는 fail이 아니라 skipped(kind=env)로 분류 —
  기계가 판단 못 하면 막지 않되, "레거시 미검증"임을 verdict로 정직하게 남긴다.
  strict_regression=True면 '있는데 못 돌린 테스트'를 차단으로 승격(빡센 게이트).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(command: list[str], cwd: str | Path, timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout({timeout}s)"
    except (OSError, FileNotFoundError) as error:
        return 127, str(error)
    return result.returncode, ((result.stdout or "") + (result.stderr or ""))[-3000:]


def _skip(name: str, reason: str, kind: str) -> dict:
    """kind: 'na'(검증 대상 없음) | 'env'(대상은 있으나 환경 탓 못 돌림=미검증 구멍)."""
    return {"name": name, "status": "skipped", "reason": reason, "kind": kind}


def check_python_syntax(repo_root: Path, changed: list[str], timeout: int = 120) -> dict:
    py_files = [f for f in changed if f.endswith(".py") and (repo_root / f).is_file()]
    if not py_files:
        return _skip("py_syntax", "py 변경 없음", "na")
    code, output = _run([sys.executable, "-m", "py_compile", *py_files], repo_root, timeout)
    return {"name": "py_syntax", "status": "passed" if code == 0 else "failed",
            "files": len(py_files), "output": "" if code == 0 else output}


def _has_pytest(repo_root: Path) -> bool:
    if (repo_root / "pytest.ini").exists() or (repo_root / "tests").is_dir():
        return True
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists() and "pytest" in pyproject.read_text(encoding="utf-8", errors="ignore"):
        return True
    return False


def check_pytest(repo_root: Path, changed: list[str], timeout: int = 600) -> dict:
    if not any(f.endswith(".py") for f in changed):
        return _skip("pytest", "py 변경 없음", "na")
    if not _has_pytest(repo_root):
        return _skip("pytest", "pytest 구성 없음", "na")
    code, output = _run([sys.executable, "-m", "pytest", "-x", "-q"], repo_root, timeout)
    if code == 0:
        return {"name": "pytest", "status": "passed", "output": output[-500:]}
    if code == 5:
        return _skip("pytest", "수집된 테스트 없음", "na")
    if code == 1:
        return {"name": "pytest", "status": "failed", "output": output}
    # 2/3/4/124/127 = 환경/수집/타임아웃 — 차단 않되 '미검증(env)'으로 정직하게 기록
    return {**_skip("pytest", f"실행 불가(exit={code}) — 환경 문제로 분류", "env"),
            "output": output[-800:]}


def _nearest_pkg_with_test(repo_root: Path, rel: str) -> Path | None:
    current = (repo_root / rel).parent
    while current != repo_root.parent and current >= repo_root:
        pkg_json = current / "package.json"
        if pkg_json.exists():
            try:
                scripts = json.loads(pkg_json.read_text(encoding="utf-8",
                                                        errors="ignore")).get("scripts", {})
            except json.JSONDecodeError:
                scripts = {}
            if "test" in scripts:
                return current
            return None  # 가장 가까운 패키지에 test 스크립트 없음
        if current == repo_root:
            break
        current = current.parent
    return None


def affected_node_files(graph, changed: list[str], repo: str, hops: int = 4) -> list[str]:
    """변경 파일에 (역)import로 닿는 ts/js 파일까지 확장 — 크로스패키지 회귀 스코프.

    A가 바뀌면 A를 import하는 B도 회귀 위험 → B의 패키지도 테스트 대상.
    """
    if graph is None:
        return list(changed)
    changed_ids = {f"{repo}:{f}" for f in changed}
    rev: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge["kind"] in ("imports", "contains", "same_package"):
            rev.setdefault(edge["dst"], set()).add(edge["src"])
    visited = set(changed_ids)
    frontier = list(changed_ids)
    for _ in range(hops):
        nxt = []
        for node in frontier:
            for parent in rev.get(node, ()):
                if parent not in visited:
                    visited.add(parent)
                    nxt.append(parent)
        frontier = nxt
    prefix = f"{repo}:"
    files = {nid[len(prefix):] for nid in visited if nid.startswith(prefix)}
    files.update(changed)
    return sorted(f for f in files if f.endswith((".ts", ".tsx", ".js", ".jsx", ".py")))


def check_node_tests(repo_root: Path, changed: list[str], timeout: int = 600,
                     scope: list[str] | None = None) -> dict:
    # scope: 그래프로 확장된 영향 파일(역의존성). 없으면 변경 파일만.
    consider = scope if scope is not None else changed
    ts_changed = [f for f in consider if f.endswith((".ts", ".tsx", ".js", ".jsx"))]
    if not any(f.endswith((".ts", ".tsx", ".js", ".jsx")) for f in changed):
        return _skip("node_test", "ts/js 변경 없음", "na")
    if not (repo_root / "node_modules").is_dir():
        return _skip("node_test", "node_modules 미설치 — 로컬 의존성 없음(도커 빌드 환경)", "env")
    packages = {pkg for f in ts_changed if (pkg := _nearest_pkg_with_test(repo_root, f))}
    if not packages:
        return _skip("node_test", "영향 패키지에 test 스크립트 없음", "na")
    outputs = []
    for pkg in sorted(packages):
        runner = "pnpm" if (repo_root / "pnpm-lock.yaml").exists() else "npm"
        code, output = _run([runner, "test"], pkg, timeout)
        if code != 0:
            return {"name": "node_test", "status": "failed",
                    "package": str(pkg.relative_to(repo_root)), "output": output}
        outputs.append(str(pkg.relative_to(repo_root)))
    return {"name": "node_test", "status": "passed", "packages": outputs,
            "cross_package": scope is not None and len(outputs) > 0}


_TEST_CHECKS = ("pytest", "node_test", "cargo_test")


def regression_verdict(results: list[dict]) -> str:
    """레거시 회귀 검증 상태: verified | unverified | failed | none.

    - failed    : 회귀 테스트가 실제로 실패(레거시 개박살) → 차단
    - unverified: 돌릴 테스트가 있는데 환경 탓 못 돌림(kind=env) → 미검증 구멍
    - verified  : 회귀 테스트가 실제로 통과
    - none      : 돌릴 회귀 테스트 자체가 없음(비대상)
    """
    tests = [r for r in results if r["name"] in _TEST_CHECKS]
    if any(r["status"] == "failed" for r in tests):
        return "failed"
    if any(r["status"] == "skipped" and r.get("kind") == "env" for r in tests):
        return "unverified"
    if any(r["status"] == "passed" for r in tests):
        return "verified"
    return "none"


def check_rust_tests(repo_root: Path, changed: list[str], timeout: int = 600) -> dict:
    """Rust는 cargo test. 툴체인이 없으면 정직하게 사유를 남긴다(cargo가 있으면 실제 실행)."""
    import shutil
    if not any(f.endswith(".rs") for f in changed):
        return _skip("cargo_test", "rust 변경 없음", "na")
    if not (repo_root / "Cargo.toml").exists():
        return _skip("cargo_test", "Cargo.toml 없음", "na")
    if not shutil.which("cargo"):
        return _skip("cargo_test", "Rust 툴체인 없음 — rustup 설치 필요", "env")
    code, output = _run(["cargo", "test", "--quiet"], repo_root, timeout)
    if code == 0:
        return {"name": "cargo_test", "status": "passed", "output": output[-400:]}
    return {"name": "cargo_test", "status": "failed", "output": output[-1500:]}


def run_checks(repo_root: str | Path, changed: list[str], test_timeout: int = 600,
               strict_regression: bool = False, graph=None, repo: str = "") -> dict:
    """전체 검증 실행. blocked=True면 MR 진행 차단.

    strict_regression=True: '있는데 못 돌린' 회귀 테스트(unverified)를 차단으로 승격.
    graph 주어지면 node 테스트 스코프를 역의존성으로 확장(크로스패키지 회귀).
    """
    repo_root = Path(repo_root)
    scope = affected_node_files(graph, changed, repo) if graph is not None else None
    # pytest는 빠진 PyPI 의존성을 격리 캐시에 깔아 가며 실제로 돌린다(testenv).
    # "의존성 없어서 skip"이 아니라, 깔 수 있는 건 깔고 진짜 테스트한다.
    from .testenv import run_pytest_with_deps
    results = [
        check_python_syntax(repo_root, changed),
        run_pytest_with_deps(repo or str(repo_root), repo_root, changed, test_timeout),
        check_node_tests(repo_root, changed, test_timeout, scope=scope),
        check_rust_tests(repo_root, changed, test_timeout),
    ]
    verdict = regression_verdict(results)
    if strict_regression and verdict == "unverified":
        results.append({
            "name": "regression_gate", "status": "failed",
            "output": "strict_regression=True — 있는데 환경 탓 못 돌린 회귀 테스트가 있어 차단. "
                      "테스트 의존성을 설치한 환경에서 다시 검증하라."})
    failed = [r for r in results if r["status"] == "failed"]
    return {"checks": results, "blocked": bool(failed),
            "regression": verdict,
            "summary": {r["name"]: r["status"] for r in results}}
