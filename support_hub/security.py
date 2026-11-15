import time


class Bucket:
    """Process-local token bucket."""
    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.tokens, self.updated = burst, time.monotonic()

    def allow(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
