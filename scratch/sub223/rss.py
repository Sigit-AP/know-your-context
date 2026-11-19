"""Shared RSS helpers for SUB-223 spike (matching prior spike's ru_maxrss metric)."""

from __future__ import annotations

import os
import resource


def rss_mib() -> float:
    """Current VmRSS in MiB."""
    with open(f"/proc/{os.getpid()}/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return -1.0


def peak_rss_mib() -> float:
    """ru_maxrss high-water in MiB (the metric the prior spike/verdict used)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def rss_delta_mib(label: str, before: float, out):
    cur = rss_mib()
    out.append(f"{label:<28} rss={cur:8.2f} MiB  delta={cur - before:7.2f} MiB")
    return cur