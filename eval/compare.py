import argparse
import json
from pathlib import Path
from typing import Any, Dict


def compare_runs(base_path: Path, new_path: Path) -> Dict[str, Any]:
    base = json.loads(base_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))

    base_cases = {c["id"]: c for c in base.get("cases", [])}
    new_cases = {c["id"]: c for c in new.get("cases", [])}

    regressions = []
    improvements = []
    for cid, nc in new_cases.items():
        bc = base_cases.get(cid)
        if not bc:
            continue
        if nc["pass_rate"] < bc["pass_rate"]:
            regressions.append({"id": cid, "base": bc["pass_rate"], "new": nc["pass_rate"]})
        elif nc["pass_rate"] > bc["pass_rate"]:
            improvements.append({"id": cid, "base": bc["pass_rate"], "new": nc["pass_rate"]})

    return {
        "base_label": base.get("label"),
        "new_label": new.get("label"),
        "pass_rate_delta": round(new.get("overall_pass_rate", 0) - base.get("overall_pass_rate", 0), 3),
        "pass_all_k_delta": round(new.get("overall_pass_all_k", 0) - base.get("overall_pass_all_k", 0), 3),
        "regressions": regressions,
        "improvements": improvements,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two eval run JSON files.")
    parser.add_argument("base", type=Path)
    parser.add_argument("new", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare_runs(args.base, args.new), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
