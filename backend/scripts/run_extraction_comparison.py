"""Extraction 비교 실험: 같은 V2.2 pipeline(prompt, schema, 검증, fallback)을 provider만 바꿔 실행한다.

DB에는 아무것도 저장하지 않는다 (Source/Candidate 생성 없음). 결과는 JSON 파일로만 남긴다.
웹 API의 Extract는 이 script와 무관하게 항상 local(llama-swap Qwen)을 쓴다.

사용 (backend 폴더에서):
  .venv/bin/python scripts/run_extraction_comparison.py --provider local --file path/to/doc.md
  .venv/bin/python scripts/run_extraction_comparison.py --provider openrouter --file a.md --file b.md
  .venv/bin/python scripts/run_extraction_comparison.py --provider openrouter --name sqlite_test --text "MVP에서는 ..."
  .venv/bin/python scripts/run_extraction_comparison.py --provider openrouter --profile optimized --file a.md
  .venv/bin/python scripts/run_extraction_comparison.py --report --file a.md --file b.md   # 저장된 결과만 비교 (API 호출 없음)

OpenRouter profile (prompt/schema/검증은 같고 요청 옵션만 다르다)
  baseline  : 처음 10개 테스트와 같은 요청 → results/extraction/openrouter/<model>/
  optimized : .env의 OPENROUTER_REASONING_EFFORT / _PROVIDER_SORT / _REQUIRE_PARAMETERS / _STAGE1·2_MAX_TOKENS
              → results/extraction/openrouter-optimized/<model>/

비용/발열 안전장치:
  - 파일은 --file로 하나씩 명시해야 한다 (폴더 전체 자동 실행 없음). 같은 파일은 한 번만 실행
  - 순차 실행 (병렬 호출 없음). 재시도는 extraction 자체 정책(Stage 1 최대 3회, Stage 2 최대 2회)뿐
  - 같은 provider/model/문서의 결과가 이미 있으면 건너뛴다 (--force로만 다시 실행)
  - openrouter로 2개 이상 실행하면 시작 전에 확인을 받는다 (--yes로 생략)
  - API Key는 출력/결과 파일에 쓰지 않는다
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import KNOWN_METRICS  # noqa: E402
from services import extraction  # noqa: E402
from services.review_trigger import format_review_trigger_v2  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "extraction"


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text).strip("_")[:80] or "doc"


def _result_dir(provider: str, profile: str | None, label: str | None = None) -> str:
    # baseline은 처음 10개 테스트 결과가 있는 기존 경로를 그대로 쓴다 (다시 실행하지 않고 비교하기 위해)
    base = f"openrouter-{profile}" if provider == "openrouter" and profile and profile != "baseline" else provider
    return f"{base}-{_slug(label)}" if label else base


def _result_path(provider: str, model: str, name: str, profile: str | None = None, label: str | None = None) -> Path:
    return RESULTS_DIR / _result_dir(provider, profile, label) / _slug(model) / f"{_slug(Path(name).stem)}.json"


def _normalized_usage(raw: dict) -> dict:
    """비교용 usage. 응답에 없던 값은 null (만들어 내지 않음)"""
    return {
        "prompt_tokens": raw.get("prompt_tokens"),
        "completion_tokens": raw.get("completion_tokens"),
        "reasoning_tokens": raw.get("completion_tokens_details.reasoning_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "cost": raw.get("cost"),
    }


def _print_result(r: dict) -> None:
    llm = r["llm"]
    calls = llm["calls"]
    print(f"\n##### [{r['provider']} / {r['model']}{' / ' + r['profile'] if r.get('profile') else ''}] {r['file']}")
    if r.get("escalation"):
        e = r["escalation"]
        print(f"  auto: final={e.get('final_provider')} escalated={e.get('escalated')} reasons={e.get('escalation_reasons')} "
              f"extractor={r.get('extractor')}" + (f" error={e.get('escalation_error')}" if e.get("escalation_error") else ""))
        for prov, run in e.get("runs", {}).items():
            print(f"    {prov}: {run['elapsed_seconds']}s calls={run['calls']} retries={run['retries']} "
                  f"title_fallbacks={run['title_fallbacks']} cost={run['usage'].get('cost')} "
                  f"stage2={[(o.get('status'), o.get('type'), o.get('cause')) for o in run['stage2_outcomes']]}")
    if r.get("profile"):
        print(f"  options reasoning={r['reasoning_effort']} sort={r['provider_sort']} require_parameters={r['require_parameters']} "
              f"max_tokens={r['max_tokens']} | served_by={r.get('served_by')} finish={r.get('finish_reasons')} truncated={r.get('truncated_calls')}")
    print(
        f"  elapsed {r['elapsed_seconds']}s | candidates {len(r['candidates'])} | "
        f"stage1 calls {calls.get('decision_sources', 0)}, stage2 calls {calls.get('review_trigger', 0)}, "
        f"anchor calls {calls.get('decision_line', 0)} | retries {llm['retries']}"
    )
    if llm["usage"]:
        print(f"  usage {llm['usage']}")
    for c in r["candidates"]:
        print(f"  - [{c['topic']}] {c['title']}")
        print(f"    decision : {c['decision']}")
        print(f"    rationale: {c['rationale']!r}")
        print(f"    trigger  : {format_review_trigger_v2(c['review_trigger'])}")
        for line in c["evidence"].split("\n"):
            print(f"    evidence {'OK ' if extraction.evidence_in_source(line, r['_content']) else 'BAD'}: {line}")
    for w in r["warnings"]:
        print(f"  warning: {w}")
    if r.get("error"):
        print(f"  ERROR: {r['error']}")


async def _run_one(provider: str, name: str, content: str, profile: str | None) -> dict:
    stats: dict = {}
    started = time.time()
    result = {"file": name, "started_at": datetime.now().isoformat(timespec="seconds")}
    if provider == "auto":
        info: dict = {}
        try:
            drafts, warnings, extractor = await extraction.extract_auto(content, KNOWN_METRICS, info)
            result.update(candidates=drafts, warnings=warnings, error=None, extractor=extractor)
        except (extraction.ExtractionError, extraction.ExtractionUnavailable) as e:
            result.update(candidates=[], warnings=[], error=str(e))
        result.update(provider="auto", model=None, elapsed_seconds=round(time.time() - started, 1), escalation=info,
                      llm={"calls": {}, "retries": {}, "usage": {}})
        return result
    try:
        drafts, warnings = await extraction.extract(content, KNOWN_METRICS, provider=provider, stats=stats, profile=profile)
        result.update(candidates=drafts, warnings=warnings, error=None)
    except (extraction.ExtractionError, extraction.ExtractionUnavailable) as e:
        result.update(candidates=[], warnings=[], error=str(e))
    llm = stats.get("llm", {"calls": {}, "retries": {}, "usage": {}})
    options = stats.get("options") or {}
    truncated = llm.get("truncated", [])
    result.update(
        provider=stats.get("provider", provider),
        model=stats.get("model", ""),
        profile=options.get("profile"),
        reasoning_effort=options.get("reasoning_effort"),
        provider_sort=options.get("provider_sort"),
        require_parameters=options.get("require_parameters"),
        max_tokens={"stage1": options.get("stage1_max_tokens"), "stage2": options.get("stage2_max_tokens")} if options else None,
        elapsed_seconds=round(time.time() - started, 1),
        usage=_normalized_usage(llm.get("usage", {})),
        served_by=llm.get("served_by"),
        finish_reasons=llm.get("finish_reasons"),
        # max_tokens 때문에 잘린 호출 (정상 추출 실패와 구분). Stage 2가 잘리면 그 조건은 manual로 보존된다
        truncated_calls=truncated,
        error_kind=("truncated" if truncated else "invalid_json" if llm.get("invalid_json") else "error") if result["error"] else None,
        llm=llm,
    )
    return result


def _report(paths: list[Path]) -> int:
    """baseline vs optimized 저장 결과 비교 (API 호출 없음)"""
    _, model, _ = extraction.provider_settings("openrouter")
    rows = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for profile in extraction.OPENROUTER_PROFILES:
            f = _result_path("openrouter", model, path.name, profile)
            if not f.exists():
                rows.append((path.name, profile, None))
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            usage = r.get("usage") or _normalized_usage(r["llm"].get("usage", {}))
            bad = sum(
                not extraction.evidence_in_source(line, content)
                for c in r["candidates"] for line in c["evidence"].split("\n") if line.strip()
            )
            rows.append((path.name, profile, {
                "sec": r["elapsed_seconds"], "cands": len(r["candidates"]), "calls": sum(r["llm"]["calls"].values()),
                "retries": sum((r["llm"].get("retries") or {}).values()), "usage": usage, "bad_evidence": bad,
                "truncated": len(r.get("truncated_calls") or []), "error": r.get("error"),
                "triggers": [format_review_trigger_v2(c["review_trigger"]) for c in r["candidates"]],
                "decisions": [c["decision"] for c in r["candidates"]],
            }))
    print(f"{'file':32} {'profile':9} {'sec':>6} {'cand':>4} {'calls':>5} {'retry':>5} {'prompt':>7} {'compl':>7} {'reason':>7} {'total':>7} {'cost':>9} {'badEv':>5} {'trunc':>5}")
    for name, profile, d in rows:
        if d is None:
            print(f"{name:32} {profile:9}  (결과 없음)")
            continue
        u = d["usage"]
        fmt = lambda v, f="{:>7}": f.format(v if v is not None else "-")  # noqa: E731
        cost = f"{u['cost']:.5f}" if u.get("cost") is not None else "-"
        print(f"{name:32} {profile:9} {d['sec']:>6} {d['cands']:>4} {d['calls']:>5} {d['retries']:>5} {fmt(u['prompt_tokens'])} "
              f"{fmt(u['completion_tokens'])} {fmt(u['reasoning_tokens'])} {fmt(u['total_tokens'])} {cost:>9} {d['bad_evidence']:>5} {d['truncated']:>5}")
    print()
    for name, profile, d in rows:
        if d:
            print(f"[{name} / {profile}]" + (f" ERROR: {d['error']}" if d["error"] else ""))
            for dec, trig in zip(d["decisions"], d["triggers"]):
                print(f"  - {dec}  |  {trig}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extraction V2.2 provider 비교 실행 (DB 저장 없음)")
    parser.add_argument("--provider", choices=(*extraction.PROVIDERS, "auto"), help="auto = local 먼저, 구조화 실패일 때만 OpenRouter")
    parser.add_argument("--profile", choices=extraction.OPENROUTER_PROFILES, help="openrouter 요청 옵션 묶음 (기본 baseline)")
    parser.add_argument("--label", help="같은 profile에서 옵션 하나만 바꾼 실험을 별도 폴더에 저장 (예: default-routing)")
    parser.add_argument("--report", action="store_true", help="저장된 baseline/optimized 결과 비교만 출력 (API 호출 없음)")
    parser.add_argument("--file", action="append", default=[], help="원문 파일 (여러 개면 --file을 반복)")
    parser.add_argument("--text", help="파일 대신 원문 문자열 (--name 필요)")
    parser.add_argument("--name", help="--text 결과 이름")
    parser.add_argument("--force", action="store_true", help="이미 결과가 있어도 다시 실행")
    parser.add_argument("--yes", action="store_true", help="openrouter 여러 개 실행 확인 생략")
    args = parser.parse_args()

    if args.report:
        if not args.file:
            parser.error("--report에는 --file이 필요합니다")
        return _report([Path(f).expanduser().resolve() for f in dict.fromkeys(args.file)])
    if not args.provider:
        parser.error("--provider가 필요합니다")
    if args.profile and args.provider != "openrouter":
        parser.error("--profile은 --provider openrouter에서만 쓸 수 있습니다 (local 요청에는 옵션을 넣지 않음)")
    profile = (args.profile or "baseline") if args.provider == "openrouter" else None

    docs: list[tuple[str, str]] = []
    if args.text:
        if not args.name:
            parser.error("--text에는 --name이 필요합니다")
        docs.append((args.name, args.text))
    seen = set()
    for f in args.file:
        path = Path(f).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        docs.append((path.name, path.read_text(encoding="utf-8")))
    if not docs:
        parser.error("--file 또는 --text가 필요합니다")

    try:
        _, model, _ = extraction.provider_settings("local" if args.provider == "auto" else args.provider)
        if args.provider == "auto":
            model = "local-first"
    except extraction.ExtractionUnavailable as e:
        print(str(e), file=sys.stderr)
        return 1

    todo = []
    for name, content in docs:
        out = _result_path(args.provider, model, name, profile, args.label)
        if out.exists() and not args.force:
            print(f"skip (이미 결과 있음, 다시 실행하려면 --force): {out}")
            continue
        todo.append((name, content, out))
    if not todo:
        return 0
    if args.provider == "openrouter" and len(todo) > 1 and not args.yes:
        answer = input(f"OpenRouter({model}, {profile})로 {len(todo)}개 문서를 순차 실행합니다 (유료). 계속할까요? [y/N] ")
        if answer.strip().lower() != "y":
            return 0

    for name, content, out in todo:  # 순차 실행 (병렬 호출 없음)
        result = asyncio.run(_run_one(args.provider, name, content, profile))
        result["label"] = args.label
        _print_result({**result, "_content": content})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
