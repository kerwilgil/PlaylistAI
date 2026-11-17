"""Process-local OAuth state and Spotify token storage for the desktop app."""

import secrets
from collections import OrderedDict
from threading import RLock
from time import monotonic


class SpotifySessions:
    """Keep bearer tokens off the browser, and serialize refresh and logout."""

    def __init__(self, *, ttl=12 * 60 * 60, state_ttl=600, capacity=128, clock=monotonic):
        self.ttl = ttl
        self.state_ttl = state_ttl
        self.capacity = capacity
        self.clock = clock
        self._tokens = OrderedDict()
        self._states = OrderedDict()
        self._lock = RLock()

    def _prune(self, entries, ttl):
        now = self.clock()
        for key, entry in list(entries.items()):
            if now - entry[0] >= ttl:
                del entries[key]

    def _put(self, entries, key, value):
        entries[key] = (self.clock(), value)
        entries.move_to_end(key)
        while len(entries) > self.capacity:
            entries.popitem(last=False)

    def new_state(self):
        with self._lock:
            self._prune(self._states, self.state_ttl)
            state = secrets.token_urlsafe(32)
            self._put(self._states, state, None)
            return state

    def consume_state(self, state):
        with self._lock:
            self._prune(self._states, self.state_ttl)
            if not isinstance(state, str) or state not in self._states:
                return False
            del self._states[state]
            return True

    def create(self, token):
        with self._lock:
            self._prune(self._tokens, self.ttl)
            sid = secrets.token_urlsafe(32)
            self._put(self._tokens, sid, dict(token))
            return sid

    def discard(self, sid):
        with self._lock:
            if isinstance(sid, str):
                self._tokens.pop(sid, None)

    def contains(self, sid):
        with self._lock:
            self._prune(self._tokens, self.ttl)
            return isinstance(sid, str) and sid in self._tokens

    def resolve(self, sid, oauth):
        # The lock prevents two refreshes or logout racing with a refresh.
        with self._lock:
            self._prune(self._tokens, self.ttl)
            if not isinstance(sid, str) or sid not in self._tokens:
                return None
            _, token = self._tokens[sid]
            try:
                if oauth.is_token_expired(token):
                    token = oauth.refresh_access_token(token["refresh_token"])
            except Exception:  # noqa: BLE001 - invalidate on any refresh failure
                self._tokens.pop(sid, None)
                return None
            self._put(self._tokens, sid, token)
            return dict(token)
