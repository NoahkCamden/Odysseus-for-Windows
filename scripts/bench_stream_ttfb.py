import json
import statistics
import time
import httpx

BASE = "http://127.0.0.1:7004"
SESSION = "6b6325fe-3543-4c36-b984-f44c3c25bb6e"
OWNER = "admin"
RUNS = 12

payload = {
    "message": "Give a one-sentence hello.",
    "session": SESSION,
    "mode": "chat",
    "use_web": "false",
    "use_research": "false",
    "compare_mode": "false",
    "incognito": "false",
}
headers = {
    "X-Odysseus-Internal-Token": "bench-token",
    "X-Odysseus-Owner": OWNER,
}

first_event_times = []
first_delta_times = []

with httpx.Client(timeout=60.0, headers=headers) as client:
    for i in range(RUNS):
        t0 = time.perf_counter()
        got_event = None
        got_delta = None
        with client.stream("POST", f"{BASE}/api/chat_stream", data=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", "replace")
                if line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                data_part = line[6:]
                if data_part == "[DONE]":
                    break
                if got_event is None:
                    got_event = (time.perf_counter() - t0) * 1000.0
                try:
                    obj = json.loads(data_part)
                except Exception:
                    obj = {}
                if got_delta is None and isinstance(obj, dict) and obj.get("delta"):
                    got_delta = (time.perf_counter() - t0) * 1000.0
                    break
                if got_event is not None and got_delta is not None:
                    break
        if got_event is not None:
            first_event_times.append(got_event)
        if got_delta is not None:
            first_delta_times.append(got_delta)


def summarize(arr):
    if not arr:
        return None
    arr2 = sorted(arr)
    n = len(arr2)
    p50 = arr2[n//2] if n % 2 == 1 else (arr2[n//2 - 1] + arr2[n//2]) / 2
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    p95 = arr2[p95_idx]
    return {
        "count": n,
        "avg_ms": round(statistics.mean(arr2), 2),
        "min_ms": round(arr2[0], 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(arr2[-1], 2),
    }

out = {
    "runs": RUNS,
    "first_event_ms": summarize(first_event_times),
    "first_delta_ms": summarize(first_delta_times),
    "raw_first_event_ms": [round(x, 2) for x in first_event_times],
    "raw_first_delta_ms": [round(x, 2) for x in first_delta_times],
}
print(json.dumps(out, indent=2))
