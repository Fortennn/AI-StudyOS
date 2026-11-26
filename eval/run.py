import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.cache import StudyResponseCache
from app.gateway import LLMGateway
from app.models import StudyRequest
from app.orchestration import StudyOrchestrator
from app.service import StudyService
from app.settings import get_settings
from app.storage import Storage


async def run_eval(
    label: str,
    k: int,
    provider: str,
    cases_path: Path,
    out_dir: Path,
    kinds_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw_bytes = cases_path.read_bytes()
    cases_hash = hashlib.sha256(raw_bytes).hexdigest()[:12]
    all_cases: List[Dict[str, Any]] = json.loads(raw_bytes.decode("utf-8"))
    cases = [c for c in all_cases if not kinds_filter or c.get("kind", "normal") in kinds_filter]

    settings = get_settings()
    settings.llm_provider = provider  # type: ignore[assignment]
    if provider == "mock":
        settings.mock_delay_ms = 5

    gateway = LLMGateway(settings=settings)
    orchestrator = StudyOrchestrator()
    storage = Storage(db_path=":memory:")
    service = StudyService(gateway=gateway, orchestrator=orchestrator, storage=storage, cache=StudyResponseCache())

    results: List[Dict[str, Any]] = []
    by_kind_acc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    total_runs = 0
    passed_runs = 0
    est_cost_per_call = 0.002 if provider == "gemini" else 0.0

    for case in cases:
        case_kind = case.get("kind", "normal")
        case_runs = []
        for run_idx in range(1, k + 1):
            total_runs += 1
            t0 = time.perf_counter()
            req = StudyRequest(
                text=case["text"],
                topic=case["topic"],
                card_count=case["card_count"],
                quiz_count=case["quiz_count"],
            )
            try:
                resp = await service.process_study_material(
                    req,
                    req_id=f"eval-{case['id']}-{run_idx}",
                    bypass_cache=True,
                )
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                passed_runs += 1
                case_runs.append(
                    {
                        "run": run_idx,
                        "passed": True,
                        "elapsed_ms": elapsed_ms,
                        "cards": len(resp.flashcards),
                        "quiz": len(resp.quiz),
                        "insufficient_data": resp.summary.insufficient_data,
                    }
                )
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                case_runs.append(
                    {
                        "run": run_idx,
                        "passed": False,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                    }
                )

        avg_ms = round(sum(r["elapsed_ms"] for r in case_runs) / max(len(case_runs), 1), 2)
        pass_rate = round(sum(1 for r in case_runs if r["passed"]) / max(len(case_runs), 1), 3)
        pass_all_k = all(r["passed"] for r in case_runs)
        case_record = {
            "id": case["id"],
            "kind": case_kind,
            "about": case.get("about", ""),
            "topic": case["topic"],
            "input_chars": len(case["text"]),
            "k": k,
            "pass_rate": pass_rate,
            "pass_all_k": pass_all_k,
            "avg_ms": avg_ms,
            "runs": case_runs,
        }
        results.append(case_record)
        by_kind_acc[case_kind].append(case_record)

    by_kind = {}
    for kname, items in by_kind_acc.items():
        k_runs = sum(len(x["runs"]) for x in items)
        k_passed = sum(sum(1 for r in x["runs"] if r["passed"]) for x in items)
        k_all = sum(1 for x in items if x["pass_all_k"])
        by_kind[kname] = {
            "cases": len(items),
            "pass_rate": round(k_passed / max(k_runs, 1), 3),
            "pass_all_k_rate": round(k_all / max(len(items), 1), 3),
        }

    overall_pass_all_k = round(sum(1 for r in results if r["pass_all_k"]) / max(len(results), 1), 3)
    report = {
        "label": label,
        "provider": provider,
        "model": settings.llm_model,
        "prompt_version": "v1",
        "cases_file": str(cases_path),
        "cases_hash": cases_hash,
        "k": k,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "overall_pass_rate": round(passed_runs / max(total_runs, 1), 3),
        "overall_pass_all_k": overall_pass_all_k,
        "estimated_cost_usd": round(total_runs * est_cost_per_call, 6),
        "by_kind": by_kind,
        "cases": results,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{label}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Eval '{label}' complete: {passed_runs}/{total_runs} passed (pass^{k}={overall_pass_all_k}) -> {out_file}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI StudyOS evaluation suite.")
    parser.add_argument("--label", default="baseline", help="Run label for eval/runs/<label>.json")
    parser.add_argument("--k", type=int, default=3, help="Repetitions per case")
    parser.add_argument("--provider", default="mock", choices=["mock", "recorded", "gemini"])
    parser.add_argument("--cases", default="eval/cases.json")
    parser.add_argument("--out-dir", default="eval/runs")
    parser.add_argument("--kinds", default="", help="Comma-separated kinds filter, e.g. adversarial,negative")
    args = parser.parse_args()

    kinds_filter = [x.strip() for x in args.kinds.split(",") if x.strip()] if args.kinds else None
    asyncio.run(
        run_eval(
            label=args.label,
            k=args.k,
            provider=args.provider,
            cases_path=Path(args.cases),
            out_dir=Path(args.out_dir),
            kinds_filter=kinds_filter,
        )
    )


if __name__ == "__main__":
    main()
