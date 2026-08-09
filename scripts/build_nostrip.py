"""주석·문자열 걷어내기만 끈 그래프를 같은 경로로 짓는다 — 그 손잡이만 비교하려고."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import xgen_maker.kg.calls as calls_mod

_original = calls_mod.scan_call_names
calls_mod.scan_call_names = lambda source, strip=True: _original(source, strip=False)

from xgen_maker.config import MakerConfig            # noqa: E402
from xgen_maker.kg.build import build_repo, merge_and_link   # noqa: E402
from xgen_maker.kg.extract_infra import extract_infra, link_infra_to_code  # noqa: E402
from xgen_maker.kg.source import resolve_ref          # noqa: E402


def main() -> None:
    config = MakerConfig.from_file("maker.config.json")
    scopes = getattr(config, "repo_scopes", None) or {}
    graphs = []
    for name, root in config.repos.items():
        if not pathlib.Path(root).is_dir():
            continue
        ref = resolve_ref(root, config.target_branch)
        graphs.append(build_repo(name, root, scopes.get(name) or None, ref=ref))
    infra = getattr(config, "infra_path", "") or ""
    if infra and pathlib.Path(infra).is_dir():
        graphs.append(extract_infra(infra))
    merged, _ = merge_and_link(graphs)
    merged.meta["infra_code_links"] = link_infra_to_code(merged)
    merged.save("kg/merged.nostrip.json")
    print(json.dumps(merged.stats()["edges_by_kind"], ensure_ascii=False))


if __name__ == "__main__":
    main()
