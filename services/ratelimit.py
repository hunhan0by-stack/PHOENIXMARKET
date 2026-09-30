"""
In-memory sliding-window rate limiter.

State lives in the process, which matches the single gunicorn worker used on
Render (see Procfile). With several workers each would keep its own counts.
"""
import threading
import time


class RateLimiter:
    _instances: list["RateLimiter"] = []

    def __init__(self, max_hits: int, window_seconds: int):
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        RateLimiter._instances.append(self)

    @classmethod
    def reset_all(cls) -> None:
        """Forget every recorded hit on every limiter (used by tests)."""
        for limiter in cls._instances:
            with limiter._lock:
                limiter._hits.clear()

    def _recent(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def retry_after(self, key: str) -> int:
        """Seconds until key may try again; 0 if it is not limited."""
        now = time.monotonic()
        with self._lock:
            hits = self._recent(key, now)
            if len(hits) < self.max_hits:
                return 0
            return max(1, int(self.window - (now - hits[0])) + 1)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 10000:
                for k in list(self._hits):
                    self._recent(k, now)
            self._hits.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
