import os
import sys
import httpx


def run_smoke(base_url: str) -> None:
    url = base_url.rstrip("/")
    demo_token = os.getenv("DEMO_TOKEN", "").strip()
    headers = {"X-Demo-Token": demo_token} if demo_token else {}

    with httpx.Client(timeout=30.0) as client:
        # 1. Health & version check
        h = client.get(f"{url}/api/health")
        assert h.status_code == 200, f"/api/health failed: {h.status_code} {h.text}"
        h_json = h.json()
        assert h_json["status"] == "ok", f"Unhealthy status: {h_json}"
        assert "git_commit" in h_json["extra"], "Missing git_commit in /api/health"
        assert "prompt_version" in h_json["extra"], "Missing prompt_version in /api/health"
        print(f"[OK] /api/health -> provider={h_json['provider']} model={h_json['model']} commit={h_json['extra']['git_commit']}")

        # 2. UI page check
        ui = client.get(f"{url}/")
        assert ui.status_code == 200, f"UI / failed: {ui.status_code}"
        print("[OK] / (UI) -> 200")

        # 3. Access control check if DEMO_TOKEN is configured
        if demo_token:
            unauth = client.post(
                f"{url}/api/generate",
                json={"text": "Тест захисту публічної адреси без токена.", "card_count": 2, "quiz_count": 2},
            )
            assert unauth.status_code == 401, f"Expected 401 without DEMO_TOKEN, got {unauth.status_code}"
            print("[OK] /api/generate without DEMO_TOKEN -> 401")

        # 4. Controlled 422 on invalid input
        bad = client.post(
            f"{url}/api/generate",
            headers=headers,
            json={"text": "мало", "card_count": 2, "quiz_count": 2},
        )
        assert bad.status_code == 422, f"Expected 422 on short input, got {bad.status_code}"
        assert bad.json()["error"]["kind"] == "invalid_input"
        print("[OK] /api/generate invalid input -> 422")

        # 5. Main AI scenario
        ok = client.post(
            f"{url}/api/generate",
            headers=headers,
            json={
                "text": "Транзакції в базах даних задовольняють властивості ACID: атомарність, узгодженість, ізольованість, довговічність.",
                "topic": "Smoke Test ACID",
                "card_count": 2,
                "quiz_count": 2,
            },
        )
        assert ok.status_code == 200, f"/api/generate failed: {ok.status_code} {ok.text}"
        ok_json = ok.json()
        assert len(ok_json["flashcards"]) == 2
        assert ok_json["provider_kind"] in ("main", "fallback", "cache", "mock", "recorded")
        print(f"[OK] /api/generate -> provider_kind={ok_json['provider_kind']} cards={len(ok_json['flashcards'])}")

    print("SMOKE CHECK PASSED")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke.py <BASE_URL>", file=sys.stderr)
        sys.exit(2)
    try:
        run_smoke(sys.argv[1])
    except Exception as exc:
        print(f"SMOKE CHECK FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
