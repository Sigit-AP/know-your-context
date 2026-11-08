"""Admission-bucket eviction guards (TASK2 WS-E: finding D2).

D2 (cluster-a verdict): when the per-actor token-bucket map hit its cap of
512 actors, ``_admit`` called ``self._buckets.clear()`` — resetting EVERY
actor's rate budget, so all 512 active spammers instantly received a full
fresh burst. The bound is correct; the eviction policy was the bug. The map
now evicts only the least-recently-used actor: existing actors keep their
budgets when a new actor arrives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _serve_helpers import make_config
from know_your_context.serve.budgets import BUCKET_CACHE_CAP
from know_your_context.serve.superpower import ServerBusy, SuperpowerDaemon

# A near-zero refill rate keeps an exhausted bucket exhausted for the whole
# test run (one token refills after 100 s; the test never waits).
_RATE = 0.01


def _daemon(tmp_path: Path) -> SuperpowerDaemon:
    config = make_config(tmp_path, agent_rate_per_second=_RATE, agent_burst=1)
    return SuperpowerDaemon(config)


def test_existing_actor_budget_survives_a_new_actor(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    # Fill the map to the cap; every actor spends its single burst token.
    for i in range(BUCKET_CACHE_CAP):
        daemon._admit(f"actor-{i}")
    # A second call from a cached actor is over-rate and refused typed.
    with pytest.raises(ServerBusy):
        daemon._admit("actor-1")
    # The 513th actor triggers eviction — of exactly one LRU entry.
    daemon._admit("actor-new")
    assert len(daemon._buckets) == BUCKET_CACHE_CAP
    # The surviving cached actor's budget was NOT reset by the eviction...
    with pytest.raises(ServerBusy):
        daemon._admit("actor-1")
    # ...the evicted LRU actor (actor-0) starts fresh, like any new actor...
    daemon._admit("actor-0")
    # ...and the newest actor also holds exactly its own burst, not more.
    with pytest.raises(ServerBusy):
        daemon._admit("actor-new")


def test_lru_eviction_protects_recently_active_actors(tmp_path: Path) -> None:
    """The kill criterion: a burst of NEW actors must not hand a fresh
    burst back to an active spammer (the old clear() did exactly that)."""
    daemon = _daemon(tmp_path)
    # Fill the map to exactly the cap, the spammer most recent.
    for i in range(BUCKET_CACHE_CAP - 1):
        daemon._admit(f"filler-{i}")
    daemon._admit("spammer")  # spends the spammer's only token
    with pytest.raises(ServerBusy):
        daemon._admit("spammer")
    # The spammer is the most-recently-used entry, so a new actor evicts
    # the oldest filler — the spammer's exhausted bucket survives.
    daemon._admit("newcomer-0")
    with pytest.raises(ServerBusy):
        daemon._admit("spammer")
    # And the evicted filler — not the survivors — is the only reset budget.
    with pytest.raises(ServerBusy):
        daemon._admit("filler-1")
    daemon._admit("filler-0")  # evicted earlier: fresh bucket, like any new actor
