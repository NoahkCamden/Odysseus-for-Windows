import contextvars
import threading
import time
from collections import deque
from typing import Any, Dict, Optional


_request_metrics_var: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "odysseus_request_metrics",
    default=None,
)

_ROLLING_LIMIT = 200
_perf_lock = threading.Lock()
_request_history = deque(maxlen=_ROLLING_LIMIT)
_route_stats: Dict[str, Dict[str, Any]] = {}
_db_totals: Dict[str, Any] = {
    "query_count": 0,
    "total_ms": 0.0,
    "slow_count": 0,
    "max_ms": 0.0,
}


def begin_request_metrics(method: str, path: str) -> contextvars.Token:
    now = time.perf_counter()
    metrics = {
        "method": method,
        "path": path,
        "started_at": now,
        "db_count": 0,
        "db_total_ms": 0.0,
        "db_max_ms": 0.0,
        "status_code": None,
        "duration_ms": 0.0,
    }
    return _request_metrics_var.set(metrics)


def finish_request_metrics(token: contextvars.Token, status_code: int) -> Optional[Dict[str, Any]]:
    metrics = _request_metrics_var.get()
    try:
        if metrics is None:
            return None
        metrics = dict(metrics)
        metrics["status_code"] = status_code
        metrics["duration_ms"] = round((time.perf_counter() - metrics["started_at"]) * 1000.0, 2)
        _record_request(metrics)
        return metrics
    finally:
        _request_metrics_var.reset(token)


def record_db_query(duration_ms: float) -> None:
    metrics = _request_metrics_var.get()
    if metrics is not None:
        metrics["db_count"] += 1
        metrics["db_total_ms"] = round(metrics["db_total_ms"] + duration_ms, 2)
        metrics["db_max_ms"] = max(metrics["db_max_ms"], round(duration_ms, 2))

    with _perf_lock:
        _db_totals["query_count"] += 1
        _db_totals["total_ms"] = round(_db_totals["total_ms"] + duration_ms, 2)
        _db_totals["max_ms"] = max(_db_totals["max_ms"], round(duration_ms, 2))
        if duration_ms >= 100.0:
            _db_totals["slow_count"] += 1


def get_perf_snapshot() -> Dict[str, Any]:
    with _perf_lock:
        request_history = list(_request_history)
        route_stats = {key: dict(value) for key, value in _route_stats.items()}
        db_totals = dict(_db_totals)

    return {
        "requests": {
            "sample_size": len(request_history),
            "recent": request_history[-20:],
            "routes": route_stats,
        },
        "database": {
            **db_totals,
            "avg_ms": round((db_totals["total_ms"] / db_totals["query_count"]) if db_totals["query_count"] else 0.0, 2),
        },
    }


def reset_perf_snapshot() -> None:
    with _perf_lock:
        _request_history.clear()
        _route_stats.clear()
        _db_totals.update({
            "query_count": 0,
            "total_ms": 0.0,
            "slow_count": 0,
            "max_ms": 0.0,
        })


def _record_request(metrics: Dict[str, Any]) -> None:
    sample = {
        "method": metrics["method"],
        "path": metrics["path"],
        "status_code": metrics["status_code"],
        "duration_ms": metrics["duration_ms"],
        "db_count": metrics["db_count"],
        "db_total_ms": metrics["db_total_ms"],
        "db_max_ms": metrics["db_max_ms"],
    }
    key = f"{sample['method']} {sample['path']}"

    with _perf_lock:
        _request_history.append(sample)
        route_stat = _route_stats.setdefault(key, {
            "count": 0,
            "errors": 0,
            "total_ms": 0.0,
            "max_ms": 0.0,
            "db_total_ms": 0.0,
            "db_query_count": 0,
        })
        route_stat["count"] += 1
        route_stat["total_ms"] = round(route_stat["total_ms"] + sample["duration_ms"], 2)
        route_stat["max_ms"] = max(route_stat["max_ms"], sample["duration_ms"])
        route_stat["db_total_ms"] = round(route_stat["db_total_ms"] + sample["db_total_ms"], 2)
        route_stat["db_query_count"] += sample["db_count"]
        if sample["status_code"] >= 500:
            route_stat["errors"] += 1
        route_stat["avg_ms"] = round(route_stat["total_ms"] / route_stat["count"], 2)
        route_stat["avg_db_ms"] = round(route_stat["db_total_ms"] / route_stat["count"], 2)
