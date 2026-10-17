"""
Rate limits per client address on the endpoints that take guesses: starting a sign-in (it
sends mail), typing a code, opening a link.

The 6-digit code is already bounded per request (five attempts, then the request locks);
these limits bound what one address can do across many requests: mail bombing an address,
or spreading code guesses over fresh requests. Counters live in the process: a deployment
with several API processes multiplies the limits by their number, which keeps them
meaningful for one self-hosted instance; a shared store comes with the hosted service.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from floppy_server.errors import ApiError

MAX_TRACKED = 50_000


@dataclass(frozen=True)
class Limit:
    """At most `count` hits per `seconds`."""

    count: int
    seconds: float


class RateLimiter:
    """Sliding-window counters per (rule, address), in memory, with a bound on addresses."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

    def hit(self, rule: str, address: str, limits: tuple[Limit, ...]) -> float | None:
        """Counts a hit; returns the seconds to wait when a limit is exceeded, else None."""
        now = self._clock()
        key = (rule, address)
        hits = self._hits.pop(key, None) or deque()
        longest = max(limit.seconds for limit in limits)
        while hits and now - hits[0] >= longest:
            hits.popleft()
        wait = None
        for limit in limits:
            recent = [t for t in hits if now - t < limit.seconds]
            if len(recent) >= limit.count:
                retry = limit.seconds - (now - recent[0])
                wait = max(wait or 0.0, retry)
        if wait is None:
            hits.append(now)
        self._hits[key] = hits
        while len(self._hits) > MAX_TRACKED:
            self._hits.popitem(last=False)
        return wait


def rate_limited(retry_after: float) -> ApiError:
    """429: too many attempts from this address; `Retry-After` says when to try again."""
    seconds = max(1, int(retry_after + 0.999))
    return ApiError(
        429,
        "rate_limited",
        "Too many attempts from this address",
        {"retryAfter": seconds},
        headers={"Retry-After": str(seconds)},
    )
