"""설정·문서 파일 추출 — 코드만 그래프에 넣으면 고칠 자리의 일부가 안 보인다.

지금까지 그래프는 py·ts·rs만 담았다. 그런데 실제로 머지된 MR을 세어 보니, 코드가
아닌 파일만 고친 것이 상당수였다(pyproject.toml 버전 범프, README 갱신, 배포 스크립트,
서비스 라우팅 yaml…). 그런 요청이 오면 착지할 좌표 자체가 없다.

파싱하지 않는다 — 목표는 완전한 구조 이해가 아니라 **착지 가능한 좌표**다. 파일 노드
하나와, 그 파일이 담은 이름들(설정 키·제목)을 검색 가능한 형태로 남긴다. 그것만으로
"이 설정 고쳐줘"가 그 파일에 닿는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from .graph import Graph

CONFIG_EXTS = {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env", ".sh", ".md"}

# 설정 키처럼 보이는 것: 줄 앞의 `key:` / `key =` / `[section]` / `"key":`
_YAML_KEY = re.compile(r"^\s*([A-Za-z_][\w.-]{2,})\s*:", re.M)
# 값도 내용이다. 라우팅 설정에서 "어느 모듈이 등록돼 있나"의 답은 키가 아니라
# 리스트 항목이다(`- admin`, `- ocr`). 사람은 그 이름으로 묻는다 —
# "실존 모듈 5종이 화이트리스트에 없어"처럼. 키만 담으면 그 요청이 이 파일에 못 닿는다.
_YAML_ITEM = re.compile(r"^\s*-\s+([A-Za-z_][\w.-]{2,})\s*$", re.M)
# `key: value` 의 값도 식별자다우면 담는다(호스트·URL·불리언은 제외).
_YAML_VALUE = re.compile(r"^\s*[A-Za-z_][\w.-]*\s*:\s*([A-Za-z_][\w.-]{2,})\s*$", re.M)
_NOT_VALUES = frozenset("true false null none yes no on off".split())
_TOML_KEY = re.compile(r"^\s*(?:\[([^\]]+)\]|([A-Za-z_][\w.-]{2,})\s*=)", re.M)
_JSON_KEY = re.compile(r'"([A-Za-z_][\w.-]{2,})"\s*:')
_MD_HEAD = re.compile(r"^#{1,3}\s+(.+)$", re.M)
_SH_FUNC = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]{2,})\s*\(\)\s*\{", re.M)

# 한 파일에서 이만큼까지만 — 큰 lock 파일이 색인을 지배하지 않게 한다.
_MAX_KEYS = 120
# 이보다 큰 파일은 생성물(lock·번들)로 보고 이름만 남긴다.
_MAX_BYTES = 400_000


def _names(rel: str, source: str) -> list[str]:
    suffix = Path(rel).suffix.lower()
    found: list[str] = []
    if suffix in (".yaml", ".yml"):
        found = (_YAML_KEY.findall(source) + _YAML_ITEM.findall(source)
                 + [v for v in _YAML_VALUE.findall(source)
                    if v.lower() not in _NOT_VALUES])
    elif suffix == ".toml":
        found = [a or b for a, b in _TOML_KEY.findall(source)]
    elif suffix == ".json":
        found = _JSON_KEY.findall(source)
    elif suffix == ".md":
        found = [h.strip() for h in _MD_HEAD.findall(source)]
    elif suffix == ".sh":
        found = _SH_FUNC.findall(source)
    else:
        found = _YAML_KEY.findall(source)
    seen: dict[str, int] = {}
    for name in found:
        name = str(name).strip()
        if name:
            seen[name] = seen.get(name, 0) + 1
    return sorted(seen, key=lambda n: (-seen[n], n))[:_MAX_KEYS]


def extract_config_file(graph: Graph, repo: str, repo_root: Path, rel: str,
                        known_files: set[str] | None = None, src=None) -> None:
    """설정·문서 파일 하나를 좌표로 남긴다(구조 해석 없이 이름만)."""
    try:
        source = (src.read_text(rel) if src is not None
                  else (repo_root / rel).read_text(encoding="utf-8-sig", errors="ignore"))
    except OSError:
        return
    file_id = f"{repo}:{rel}"
    meta = {"lang": "config"}
    if len(source) <= _MAX_BYTES:
        keys = _names(rel, source)
        if keys:
            # 무엇이 적혀 있는지로도 이 파일을 찾을 수 있어야 한다.
            meta["refs"] = " ".join(keys)
    graph.add_node(file_id, "file", Path(rel).name, repo, rel, **meta)
    graph.add_edge(repo, file_id, "contains")
