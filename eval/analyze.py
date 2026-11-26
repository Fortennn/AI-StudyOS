import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from app.cache import StudyResponseCache
from app.gateway import LLMGateway
from app.models import StudyRequest
from app.orchestration import StudyOrchestrator
from app.service import StudyService
from app.settings import Settings
from app.storage import Storage


def analyze_jsonl(log_path: Path) -> Dict[str, Any]:
    if not log_path.exists():
        return {"events": 0, "error": f"Log file not found: {log_path}"}

    events: List[Dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    durations = [float(e["duration_ms"]) for e in events if isinstance(e.get("duration_ms"), (int, float))]
    p50_ms = round(statistics.median(durations), 2) if durations else 0.0
    if len(durations) >= 20:
        p95_ms = round(statistics.quantiles(durations, n=20)[18], 2)
    elif durations:
        p95_ms = round(sorted(durations)[max(0, int(len(durations) * 0.95) - 1)], 2)
    else:
        p95_ms = 0.0

    by_stage = Counter(e.get("stage", "unknown") for e in events)
    by_kind = Counter(e.get("kind") for e in events if e.get("kind"))
    by_provider_kind = Counter(e.get("provider_kind") for e in events if e.get("provider_kind"))
    cache_hits = sum(1 for e in events if e.get("cached") is True)
    retries = sum(1 for e in events if (e.get("attempt") or 1) > 1)
    saved_by_retry = sum(1 for e in events if (e.get("attempt") or 1) > 1 and e.get("status") == "ok")
    total_cost = round(sum(float(e.get("cost") or 0.0) for e in events), 6)

    return {
        "events": len(events),
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "by_stage": dict(by_stage),
        "failures_by_kind": dict(by_kind),
        "by_provider_kind": dict(by_provider_kind),
        "cache_hits": cache_hits,
        "retries": retries,
        "saved_by_retry": saved_by_retry,
        "total_estimated_cost_usd": total_cost,
    }


async def compare_cache_runs(cases_path: Path) -> Dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    # Simulate a stream with 2x repeated requests per case
    stream = cases + cases

    async def _run(use_cache: bool) -> Dict[str, Any]:
        settings = Settings(llm_provider="mock", mock_delay_ms=10, budget_file="data/budget_eval.json")
        gw = LLMGateway(settings=settings)
        svc = StudyService(gateway=gw, orchestrator=StudyOrchestrator(), storage=Storage(":memory:"), cache=StudyResponseCache())
        t0 = time.perf_counter()
        provider_calls = 0
        cache_hits = 0
        for idx, c in enumerate(stream):
            req = StudyRequest(text=c["text"], topic=c["topic"], card_count=c["card_count"], quiz_count=c["quiz_count"])
            res = await svc.process_study_material(req, req_id=f"cmp-{idx}", bypass_cache=not use_cache)
            if res.provider_kind == "cache":
                cache_hits += 1
            else:
                provider_calls += 1
        return {
            "requests": len(stream),
            "provider_calls": provider_calls,
            "cache_hits": cache_hits,
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    without_cache = await _run(use_cache=False)
    with_cache = await _run(use_cache=True)
    saved_calls = without_cache["provider_calls"] - with_cache["provider_calls"]
    return {"without_cache": without_cache, "with_cache": with_cache, "saved_provider_calls": saved_calls}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze structured JSONL logs and cache impact.")
    parser.add_argument("--log-file", default="logs/app.jsonl")
    parser.add_argument("--compare-cache", action="store_true")
    parser.add_argument("--cases", default="eval/cases.json")
    args = parser.parse_args()

    summary = analyze_jsonl(Path(args.log_file))
    if args.compare_cache:
        summary["cache_comparison"] = asyncio.run(compare_cache_runs(Path(args.cases)))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
