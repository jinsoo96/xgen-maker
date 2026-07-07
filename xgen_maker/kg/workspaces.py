"""pnpm 모노레포 워크스페이스 + tsconfig path alias 해석 — TS 임포트 resolution 강화.

- 워크스페이스: repo 내 package.json의 "name" → 패키지 디렉토리 맵.
  임포트 spec이 워크스페이스명이면 feature 단위로 연결한다.
- alias: tsconfig*.json의 compilerOptions.paths ("@/*": ["./src/*"]) 해석.
  tsconfig JSON은 주석/트레일링콤마 허용이라 관대한 파서 사용.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_SKIP = {"node_modules", ".git", ".next", "dist", "build", ".turbo", "coverage"}


def _tolerant_json(text: str) -> dict | None:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_configs(repo_root: Path, filename_prefix: str, max_depth: int = 6):
    stack = [(repo_root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP and not entry.name.startswith(".") and depth < max_depth:
                    stack.append((entry, depth + 1))
            elif entry.name.startswith(filename_prefix) and entry.suffix == ".json":
                yield entry


def scan_workspaces(repo_root: Path) -> dict[str, str]:
    """package.json name → 패키지 루트(rel posix)."""
    workspaces: dict[str, str] = {}
    for pkg_json in _walk_configs(repo_root, "package"):
        if pkg_json.name != "package.json":
            continue
        data = _tolerant_json(pkg_json.read_text(encoding="utf-8", errors="ignore") or "")
        name = (data or {}).get("name")
        if name and pkg_json.parent != repo_root:
            workspaces[name] = pkg_json.parent.relative_to(repo_root).as_posix()
    return workspaces


def scan_aliases(repo_root: Path) -> list[tuple[str, list[str]]]:
    """(alias prefix, [target dir rel posix]) 목록. 구체적(긴) prefix 우선 정렬."""
    aliases: list[tuple[str, list[str]]] = []
    for ts_json in _walk_configs(repo_root, "tsconfig"):
        data = _tolerant_json(ts_json.read_text(encoding="utf-8", errors="ignore") or "")
        options = (data or {}).get("compilerOptions", {})
        paths = options.get("paths", {})
        base = ts_json.parent
        base_url = options.get("baseUrl", ".")
        for pattern, targets in paths.items():
            prefix = pattern[:-1] if pattern.endswith("*") else pattern
            resolved: list[str] = []
            for target in targets if isinstance(targets, list) else []:
                target = target[:-1] if target.endswith("*") else target
                try:
                    target_dir = (base / base_url / target).resolve().relative_to(
                        repo_root.resolve()).as_posix()
                except ValueError:
                    continue
                resolved.append(target_dir)
            if resolved:
                aliases.append((prefix, resolved))
    aliases.sort(key=lambda pair: -len(pair[0]))
    return aliases


_TS_EXTS = (".ts", ".tsx", ".js", ".jsx")


class ImportResolver:
    """TS 임포트 spec → ('file', relpath) | ('feature', 워크스페이스명) | None."""

    def __init__(self, repo_root: Path, known_files: set[str],
                 workspaces: dict[str, str] | None = None,
                 aliases: list[tuple[str, list[str]]] | None = None):
        self.repo_root = repo_root
        self.known = known_files
        self.workspaces = workspaces or {}
        self.aliases = aliases or []

    def _try_file(self, base: str) -> str | None:
        base = base.rstrip("/")
        candidates = ([base] if Path(base).suffix else []) + \
            [base + ext for ext in _TS_EXTS] + [f"{base}/index{ext}" for ext in _TS_EXTS]
        for candidate in candidates:
            if candidate in self.known:
                return candidate
        return None

    def resolve(self, spec: str, rel: str) -> tuple[str, str] | None:
        if spec.startswith("."):
            parts: list[str] = []
            for part in (Path(rel).parent / spec).as_posix().split("/"):
                if part == "..":
                    if parts:
                        parts.pop()
                elif part not in (".", ""):
                    parts.append(part)
            found = self._try_file("/".join(parts))
            return ("file", found) if found else None
        for prefix, targets in self.aliases:
            if spec.startswith(prefix):
                remainder = spec[len(prefix):].lstrip("/")
                for target in targets:
                    found = self._try_file(f"{target}/{remainder}" if remainder else target)
                    if found:
                        return ("file", found)
        for name, _dir in self.workspaces.items():
            if spec == name or spec.startswith(name + "/"):
                return ("feature", name)
        return None
