"""The ``know-your-context`` command-line interface (Task 7).

Task 7 of ``docs/superpowers/plans/2026-10-24-superpower-knowledge-soul.md``;
spec sections 7, 8 and 13.1.

A thin adapter over :mod:`know_your_context.http_api`'s shared dispatch: every
command validates its JSON payload, invokes the fourteen-operation
``KnowledgeService`` (the frozen ten v1 operations plus the contract-v2 G1
retrieval verbs ``path`` / ``explain`` / ``affected`` / ``god_nodes`` —
operator ruling 2026-10-26, addendum ``docs/contracts/retrieval-verbs-v2.md``)
(or the operational commands ``rebuild``, ``backup``,
``restore``, ``health`` and the operator-gated relearn lifecycle
``relearn-scan``/``relearn-status``/``relearn-preview``/``relearn-apply``)
through the same code path as the HTTP API, prints the canonical JSON result
on stdout, and prints the consistent error envelope on stderr. Exit codes:

* ``0`` — success (``health`` additionally requires ``ok: true``);
* ``1`` — the service rejected a valid request (typed error, journaled by the
  service exactly once);
* ``2`` — the adapter rejected invalid input (journaled with exactly one
  completion audit record by the adapter).

``serve`` starts the persistent local API: a Unix socket by default, or
opt-in loopback HTTP with ``--http HOST:PORT``; wider binds fail closed
without ``--api-key``. Prefer ``--api-key-env NAME`` over ``--api-key`` — an
inline key is visible to every local user via process argv (wave-2 F6).
``--ops-root`` confines ``backup``/``restore`` paths (default: the store's
directory; wave-2 F1).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from know_your_context import __version__
from know_your_context.domain import canonical_json
from know_your_context.http_api import (
    DEFAULT_HOST_ALLOWLIST,
    KNOWLEDGE_OPERATIONS,
    OPS_OPERATIONS,
    AdapterValidationError,
    build_stack,
    dispatch_text,
    error_envelope,
    run_extraction_verb,
    run_ingest_verb,
    serve,
    to_jsonable,
)
from know_your_context.security import BindPolicyError
from know_your_context.update_pipeline import UpdatePipelineError

__all__ = ["build_parser", "main"]

DEFAULT_STORE_PATH = "know-your-context.sqlite3"
DEFAULT_OUTPUT_ROOT = "know-your-context-projection"
DEFAULT_SOCKET_PATH = "know-your-context.sock"

STORE_ENV_VAR = "KNOW_YOUR_CONTEXT_STORE"


def _default_store_path() -> str:
    """Store path default: KNOW_YOUR_CONTEXT_STORE, then the built-in default."""
    return os.environ.get(STORE_ENV_VAR) or DEFAULT_STORE_PATH


def _add_webview_parsers(subparsers: Any) -> None:
    """The ``webview`` lifecycle + ``setup`` install flow (W7 SUB-101, W17 SUB-138)."""
    webview_parser = subparsers.add_parser(
        "webview",
        help="ops: open the loopback-only token-authenticated web viewer (not a knowledge op)",
    )
    webview_parser.add_argument(
        "webview_action",
        nargs="?",
        choices=("on", "off", "status"),
        default=None,
        help="lifecycle: on (persist + serve), off (persist off), status (probe); bare opens per saved config",
    )
    webview_parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port; 0 picks a free one (default)",
    )
    webview_parser.add_argument(
        "--no-open",
        action="store_true",
        help="print the URL but do not open a browser",
    )
    webview_parser.add_argument(
        "--serve-once",
        action="store_true",
        help="start, print the URL, exit (smoke/CI; used by tests)",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="ops: first-run install — init store, probe hardware, verify chain, live webview progress",
    )
    setup_parser.add_argument(
        "--no-webview",
        dest="serve_webview",
        action="store_false",
        help="run the install without the webview (kill switch)",
    )
    setup_parser.add_argument("--port", type=int, default=0, help="webview port during install (default: free)")
    setup_parser.add_argument(
        "--hold",
        action="store_true",
        help="after install, keep serving while the persisted preference enables the webview",
    )


def _add_lease_parser(subparsers: Any) -> None:
    """The ``lease`` node-lease lifecycle (W17 SUB-138)."""
    lease_parser = subparsers.add_parser(
        "lease",
        help="ops: node leases for multi-agent write coordination (not a knowledge op)",
    )
    lease_sub = lease_parser.add_subparsers(dest="lease_action", required=True)
    lease_sub.add_parser("list", help="list active node leases as canonical JSON")
    lease_acquire = lease_sub.add_parser("acquire", help="claim a node for an actor")
    lease_acquire.add_argument("node_id", help="the node to claim")
    lease_acquire.add_argument("--actor", required=True, help="the claiming agent")
    lease_acquire.add_argument(
        "--ttl",
        type=float,
        default=None,
        help="lease lifetime in seconds (default: the engine default)",
    )
    lease_release = lease_sub.add_parser("release", help="release a node you hold")
    lease_release.add_argument("node_id", help="the node to release")
    lease_release.add_argument("--actor", required=True, help="the holding agent")
    lease_force = lease_sub.add_parser("force-release", help="operator override: release regardless of holder")
    lease_force.add_argument("node_id", help="the node to force-release")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="know-your-context",
        description="Deterministic local knowledge service: fourteen audited operations plus ops commands.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--store",
        default=_default_store_path(),
        help=(f"path to the SQLite event store (default: ${STORE_ENV_VAR} or {DEFAULT_STORE_PATH})"),
    )
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT, help="projection output directory")
    parser.add_argument("--actor", default="operator", help="actor recorded on the invocation audit")
    parser.add_argument("--idempotency-key", default=None, help="idempotency key for mutation commands")
    parser.add_argument(
        "--ops-root",
        default=None,
        help="fixed containment root for backup/restore paths (default: the store's directory)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for operation in (*KNOWLEDGE_OPERATIONS, *OPS_OPERATIONS):
        sub = subparsers.add_parser(
            operation.replace("_", "-"),
            help=f"knowledge.{operation}" if operation in KNOWLEDGE_OPERATIONS else f"ops: {operation}",
        )
        sub.add_argument(
            "payload",
            nargs="?",
            default=None,
            help="JSON payload object; '-' reads it from stdin; omitted means {}",
        )

    serve_parser = subparsers.add_parser("serve", help="run the persistent local API server")
    bind = serve_parser.add_mutually_exclusive_group()
    bind.add_argument("--socket", default=None, help=f"unix socket path (default {DEFAULT_SOCKET_PATH})")
    bind.add_argument("--http", default=None, help="loopback HTTP bind as HOST:PORT (e.g. 127.0.0.1:8080)")
    key = serve_parser.add_mutually_exclusive_group()
    key.add_argument(
        "--api-key",
        default=None,
        help="API key required for non-loopback binds/clients (WARNING: visible in process argv; prefer --api-key-env)",
    )
    key.add_argument(
        "--api-key-env",
        default=None,
        metavar="NAME",
        help="read the API key from environment variable NAME (keeps it out of argv)",
    )
    serve_parser.add_argument(
        "--host-allowlist",
        default=None,
        help="comma-separated Host allowlist (default: localhost,127.0.0.1,::1)",
    )
    serve_parser.add_argument("--max-body-bytes", type=int, default=None, help="request body size cap")
    serve_parser.add_argument(
        "--watch",
        default=None,
        metavar="PATH",
        help="watch a corpus root and emit gated relearn offers on change (spec 13.5; detect-only, never applies)",
    )
    serve_parser.add_argument(
        "--watch-interval",
        type=float,
        default=30.0,
        help="seconds between watcher polls (default 30)",
    )
    serve_parser.add_argument(
        "--watch-cooldown",
        type=float,
        default=300.0,
        help="seconds the watcher pauses after emitting an offer (default 300)",
    )
    serve_parser.add_argument(
        "--watch-state",
        default=None,
        metavar="PATH",
        help="watcher snapshot path (default: alongside the store; must live outside the watched root)",
    )
    # Wave-7 SUB-100: superpower server modes. Without these flags `serve`
    # remains the HTTP/unix local API; with them it manages the detached
    # multi-agent daemon or runs the thin MCP stdio proxy.
    superpower = serve_parser.add_mutually_exclusive_group()
    superpower.add_argument(
        "--daemon",
        action="store_true",
        help="superpower: spawn the detached multi-agent daemon (double-fork/setsid) and exit",
    )
    superpower.add_argument(
        "--proxy",
        action="store_true",
        help="superpower: run the thin MCP stdio proxy (auto-spawns the daemon)",
    )
    superpower.add_argument("--daemon-foreground", action="store_true", help=argparse.SUPPRESS)
    serve_parser.add_argument(
        "--max-agents",
        type=int,
        default=None,
        help="superpower: concurrent agent cap (default: hardware-derived, historical floor 50)",
    )
    serve_parser.add_argument(
        "--read-pool-size",
        type=int,
        default=None,
        help="superpower: read-only pool size (default: hardware-derived, historical floor 8)",
    )
    serve_parser.add_argument(
        "--idle-timeout",
        type=float,
        default=300.0,
        help="superpower: seconds with no clients before the daemon exits (default 300)",
    )
    serve_parser.add_argument(
        "--capability-mode",
        choices=("off", "enforce"),
        default="off",
        help="superpower: 'off' (default) is legacy behavior; 'enforce' requires operator-issued "
        "capability tokens for every non-public verb (W10)",
    )
    serve_parser.add_argument(
        "--node-leases",
        action="store_true",
        help="superpower: guard writes with node leases — a second agent touching a leased node "
        "gets typed NODE_IN_USE instead of silently overwriting (W17)",
    )
    serve_parser.add_argument(
        "--node-lease-ttl",
        type=float,
        default=None,
        help="superpower: node lease lifetime in seconds (default 120)",
    )

    daemon_parser = subparsers.add_parser("daemon", help="superpower: inspect or stop the daemon bound to --store")
    daemon_parser.add_argument("daemon_action", choices=("status", "stop"))
    daemon_parser.add_argument(
        "--force",
        action="store_true",
        help="with stop: skip the drain window (queued work fails typed, daemon exits 2)",
    )

    # W10 (SUB-117): operator capability management. OPS surface only — never
    # a knowledge operation, never dispatched over MCP or HTTP.
    capability_parser = subparsers.add_parser(
        "capability",
        help="ops: issue, revoke and list per-agent capability tokens for the superpower daemon",
    )
    capability_sub = capability_parser.add_subparsers(dest="capability_action", required=True)
    capability_issue = capability_sub.add_parser("issue", help="issue a signed capability token for one actor")
    capability_issue_args = capability_issue  # alias for readability
    capability_issue_args.add_argument("--actor", required=True, help="the agent this token is bound to")
    capability_issue_args.add_argument(
        "--scope",
        choices=("read", "write", "admin"),
        default="read",
        help="read = public read verbs; write = read+write+ingest; admin = everything incl. daemon.* (default read)",
    )
    capability_issue_args.add_argument(
        "--verbs",
        default=None,
        help="comma-separated verb allowlist (default: every verb the scope grants)",
    )
    capability_issue_args.add_argument(
        "--ttl-seconds", type=float, default=3600.0, help="lifetime in seconds (default 3600)"
    )
    capability_issue_args.add_argument(
        "--expires-at", type=float, default=None, help="absolute expiry epoch seconds (overrides --ttl-seconds)"
    )
    capability_revoke = capability_sub.add_parser("revoke", help="revoke one issued capability immediately")
    revoke_selector = capability_revoke.add_mutually_exclusive_group(required=True)
    revoke_selector.add_argument("--nonce", default=None, help="the capability nonce (from 'capability list')")
    revoke_selector.add_argument("--token", default=None, help="a full token whose nonce should be revoked")
    capability_sub.add_parser("list", help="list issued and revoked capabilities (never prints tokens)")

    # ATM F-D1: operational export commands. These are OPS surface only —
    # they are NOT knowledge operations, never join KNOWLEDGE_OPERATIONS /
    # OPS_OPERATIONS, and are never dispatched over MCP or HTTP.
    export_parser = subparsers.add_parser(
        "export",
        help="ops: write export artifacts from the active generation (not a knowledge op)",
    )
    export_parser.add_argument(
        "--formats",
        default=None,
        help=(
            "comma-separated artifact names, short aliases (dot, mermaid, tabular, graphml, svg, html, book, "
            "gexf, cytoscape, d3force, adjacency, tgf) or 'all' (default: all registered 'format' exports)"
        ),
    )
    export_parser.add_argument(
        "--out",
        default=None,
        help="output directory (default: <output-root>/exports; resolved inside the containment root)",
    )
    analytics_parser = subparsers.add_parser(
        "analytics",
        help="ops: write analytics artifacts from the active generation (not a knowledge op)",
    )
    analytics_parser.add_argument(
        "--modules",
        dest="formats",
        default=None,
        help=(
            "comma-separated artifact names, short aliases (centrality, components, cycles, communities, "
            "bridges, review-signals, health, report-v2, generation-diff, timeline, graph-summary, hits, "
            "eigenvector, katz, clustering, louvain, reachability, motifs, assortativity, paths, aggregates, "
            "near-duplicates) or 'all' (default: all registered 'analytics' modules)"
        ),
    )
    analytics_parser.add_argument(
        "--out",
        default=None,
        help="output directory (default: <output-root>/exports; resolved inside the containment root)",
    )

    # W8 SUB-107: composite graph-analysis report (god nodes, surprising
    # connections, suggested questions, token benchmark). OPS surface only —
    # not a knowledge operation, never dispatched over MCP or HTTP.
    report_parser = subparsers.add_parser(
        "report",
        help="ops: build the composite graph analysis report from the active generation (not a knowledge op)",
    )
    report_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="stdout format (default: markdown)",
    )
    report_parser.add_argument(
        "--out",
        default=None,
        help="also write graph-report.json + graph-report.md into this directory (contained)",
    )
    report_parser.add_argument("--top-god", type=int, default=10, help="god-node ranking cap (default 10)")
    report_parser.add_argument("--top-surprising", type=int, default=10, help="surprising-connection cap (default 10)")
    report_parser.add_argument("--top-questions", type=int, default=8, help="suggested-question cap (default 8)")

    # W14 (SUB-128): knowledge debt queue — budgeted, prioritized work queue
    # with burn-down metrics and proposal-gated actions. OPS surface only —
    # never knowledge ops, never MCP/HTTP.
    debt_parser = subparsers.add_parser(
        "debt",
        help="ops: knowledge debt queue — list, report (burn-down), propose (not a knowledge op)",
    )
    debt_sub = debt_parser.add_subparsers(dest="debt_action", required=True)

    def _debt_config_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--budget", type=int, default=10, help="max queue items per cycle (1..100, default 10)")
        sub.add_argument(
            "--stale-days", type=int, default=30, help="days without use before a node is stale (default 30)"
        )
        sub.add_argument(
            "--unresolved-days", type=int, default=7, help="days before an unresolved ref is debt (default 7)"
        )
        sub.add_argument(
            "--low-score-threshold",
            default="0.250000000000",
            help="confidence below this is low-score debt (default 0.250000000000)",
        )

    debt_list = debt_sub.add_parser("list", help="print the budgeted debt queue as canonical JSON")
    _debt_config_args(debt_list)
    debt_report = debt_sub.add_parser("report", help="debt report with burn-down metrics")
    _debt_config_args(debt_report)
    debt_report.add_argument("--format", choices=("json", "markdown"), default="markdown")
    debt_report.add_argument(
        "--record", action="store_true", help="append this cycle's snapshot to the burn-down ledger"
    )
    debt_report.add_argument("--ledger", default=None, help="ledger path (default: alongside the store)")
    debt_propose = debt_sub.add_parser(
        "propose", help="turn a node's debt item into a real improvement proposal (gated)"
    )
    debt_propose.add_argument("node_id", help="node whose debt item becomes a proposal")
    _debt_config_args(debt_propose)

    # W15 (SUB-130): deep code-intelligence graph ops over the universal
    # code graph — transitive callers/callees, dependency paths, impact
    # analysis, co-change from source-version history and layer boundary
    # violations. OPS surface only — never knowledge ops, never MCP/HTTP.
    codeintel_parser = subparsers.add_parser(
        "codeintel",
        help="ops: deep code-intelligence graph queries (not a knowledge op)",
    )
    codeintel_sub = codeintel_parser.add_subparsers(dest="codeintel_action", required=True)

    def _codeintel_traversal_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("symbol", help="symbol name or node id")
        sub.add_argument("--file", default=None, help="restrict resolution to this path")
        sub.add_argument("--depth", type=int, default=8, help="max traversal depth (1..32, default 8)")
        sub.add_argument("--limit", type=int, default=200, help="max entries (1..5000, default 200)")

    ci_callers = codeintel_sub.add_parser("callers-of", help="transitive callers of a symbol")
    _codeintel_traversal_args(ci_callers)
    ci_callees = codeintel_sub.add_parser("callees-of", help="transitive callees of a symbol")
    _codeintel_traversal_args(ci_callees)
    ci_path = codeintel_sub.add_parser("dependency-path", help="shortest dependency path between two symbols")
    ci_path.add_argument("source", help="source symbol name or node id")
    ci_path.add_argument("target", help="target symbol name or node id")
    ci_impact = codeintel_sub.add_parser("impact", help="what breaks when a symbol changes")
    ci_impact.add_argument("symbol", help="symbol name or node id")
    ci_impact.add_argument("--file", default=None, help="restrict resolution to this path")
    ci_impact.add_argument("--depth", type=int, default=8, help="max traversal depth (1..32, default 8)")
    ci_impact.add_argument("--limit", type=int, default=500, help="max entries (1..5000, default 500)")
    ci_cochange = codeintel_sub.add_parser("co-change", help="files that change together (from source-version history)")
    ci_cochange.add_argument("--min-support", type=int, default=2, help="min change sets containing both (default 2)")
    ci_cochange.add_argument("--limit", type=int, default=100, help="max pairs (default 100)")
    ci_boundary = codeintel_sub.add_parser("boundary-violations", help="cross-layer edges the policy does not allow")
    ci_boundary.add_argument(
        "--layers",
        required=True,
        help="layer spec: name:prefix[,prefix...];name2:prefix... (e.g. 'ui:ui,web;core:core,lib')",
    )
    ci_boundary.add_argument(
        "--allow",
        default="",
        help="allowed cross-layer flows: from:to,from2:to2 (default: none)",
    )
    ci_boundary.add_argument("--limit", type=int, default=500, help="max violations (default 500)")

    # Mega-wave 2 OPS tooling (SUB-77 modules, wired by SUB-80): layered
    # configuration introspection, operational metrics, and the fail-closed
    # self-check. OPS surface only — never knowledge ops, never MCP/HTTP.
    config_parser = subparsers.add_parser(
        "config",
        help="ops: inspect the layered operational configuration (not a knowledge op)",
    )
    config_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="optional TOML config file (precedence: defaults < file < env < CLI)",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("describe", help="markdown table of the resolved configuration with provenance")
    config_sub.add_parser("effective", help="canonical JSON of the flat resolved key=value configuration")
    config_sub.add_parser("dump", help="canonical JSON of {key: {value, source}} with per-key provenance")

    metrics_parser = subparsers.add_parser(
        "metrics",
        help="ops: collect operational metrics over the store and active projection (not a knowledge op)",
    )
    metrics_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="output format (default: json)",
    )
    metrics_parser.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="seconds after which an in-progress journal counts as stale (default 300)",
    )

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="ops: run the read-only self-check; exit 0 when no error findings, 1 otherwise",
    )
    diagnose_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="output format (default: json)",
    )
    diagnose_parser.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="seconds after which an in-progress journal counts as stale (default 300)",
    )
    diagnose_parser.add_argument(
        "--no-permissions",
        action="store_true",
        help="skip the file-permission check (diagnose.check_permissions)",
    )

    # W12 (SUB-122): lifecycle tooling — health history, retention pruning of
    # the history ledger, fragmentation analysis and safe vacuum/compact.
    # OPS surface only — never knowledge ops, never MCP/HTTP.
    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help="ops: health history, retention pruning and safe store compaction (not a knowledge op)",
    )
    lifecycle_sub = lifecycle_parser.add_subparsers(dest="lifecycle_action", required=True)
    lifecycle_record = lifecycle_sub.add_parser("record", help="append one hash-chained health snapshot to the ledger")
    lifecycle_record.add_argument("--history", default=None, help="ledger path (default: alongside the store)")
    lifecycle_trend = lifecycle_sub.add_parser("trend", help="analyze the ledger's health trend")
    lifecycle_trend.add_argument("--history", default=None, help="ledger path (default: alongside the store)")
    lifecycle_prune = lifecycle_sub.add_parser("prune", help="retain only the newest N ledger snapshots")
    lifecycle_prune.add_argument("--keep", type=int, required=True, help="snapshots to retain (>= 0)")
    lifecycle_prune.add_argument("--history", default=None, help="ledger path (default: alongside the store)")
    lifecycle_prune.add_argument("--dry-run", action="store_true", help="report the plan without rewriting the ledger")
    lifecycle_sub.add_parser("vacuum", help="compact the store file (verified staging copy + atomic swap)")
    lifecycle_sub.add_parser("fragmentation", help="report page/freelist statistics of the store file")

    # W12 (SUB-122): deep diagnostics with structured repair suggestions —
    # goes beyond `diagnose` (store/projection state) into environment,
    # dependencies, permissions, integrity drills and performance probes.
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="ops: deep environment/dependency/permission/integrity/performance diagnostic (not a knowledge op)",
    )
    doctor_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="output format (default: json)",
    )
    doctor_parser.add_argument(
        "--full-integrity",
        action="store_true",
        help="run PRAGMA integrity_check (expensive) in addition to quick_check",
    )
    doctor_parser.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="seconds after which an in-progress journal counts as stale (default 300)",
    )

    # W12 (SUB-122): store version migration and portable hashed bundles.
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="ops: inspect/plan/apply schema migrations; export/import portable hashed bundles (not a knowledge op)",
    )
    migrate_sub = migrate_parser.add_subparsers(dest="migrate_action", required=True)
    migrate_sub.add_parser("plan", help="show the pending migration steps for --store")
    migrate_sub.add_parser("apply", help="apply pending migrations to --store and verify the chain")
    migrate_export = migrate_sub.add_parser("export", help="export --store as a portable hashed bundle")
    migrate_export.add_argument("--dest", required=True, help="bundle directory (must not exist or be empty)")
    migrate_import = migrate_sub.add_parser("import", help="verify and install a bundle")
    migrate_import.add_argument("--bundle", required=True, help="bundle directory to install")
    migrate_import.add_argument("--dest", default=None, help="destination store path (default: --store)")
    migrate_import.add_argument("--overwrite", action="store_true", help="replace an existing destination store")
    migrate_verify = migrate_sub.add_parser("verify", help="verify a bundle's manifest, hashes and payload")
    migrate_verify.add_argument("--bundle", required=True, help="bundle directory to verify")

    # W12 (SUB-122): multi-store status aggregation.
    watchtower_parser = subparsers.add_parser(
        "watchtower",
        help="ops: aggregate the status of many stores into one report (not a knowledge op)",
    )
    watchtower_parser.add_argument("stores", nargs="*", help="store paths to probe (name defaults to the file stem)")
    watchtower_parser.add_argument(
        "--target",
        action="append",
        default=None,
        metavar="NAME=PATH",
        help="explicitly named store target (repeatable)",
    )
    watchtower_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="output format (default: json)",
    )
    watchtower_parser.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="seconds after which an in-progress journal counts as stale (default 300)",
    )

    # W7 SUB-101 + W17 SUB-138: loopback read-only web viewer + lifecycle + install flow (ops surface).
    _add_webview_parsers(subparsers)

    # Wave-2 SUB-89: universal-graph ingestion. OPS surface only — NOT a
    # knowledge operation, never joins KNOWLEDGE_OPERATIONS / OPS_OPERATIONS,
    # never dispatched over MCP or HTTP; the frozen fourteen are untouched.
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="ops: ingest a file or directory into the v3 ingestion tables (proposal-only, not a knowledge op)",
    )
    ingest_parser.add_argument("path", help="file or directory to ingest (the ingestion root)")
    ingest_parser.add_argument(
        "--incremental",
        dest="incremental",
        action="store_true",
        default=True,
        help="skip unchanged files via the (size, mtime) pre-filter + sha256 confirm (default)",
    )
    ingest_parser.add_argument(
        "--no-incremental",
        dest="incremental",
        action="store_false",
        help="force a full re-ingest of every file",
    )
    ingest_parser.add_argument(
        "--recursive",
        dest="recursive",
        action="store_true",
        default=True,
        help="walk subdirectories (default)",
    )
    ingest_parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="only the top level")
    ingest_parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="park cross-file references without running the resolve pass",
    )
    ingest_parser.add_argument("--max-files", type=int, default=1024, help="walk cap, [1, 65536] (default 1024)")
    ingest_parser.add_argument("--max-nodes", type=int, default=65536, help="fail-closed total node cap")
    ingest_parser.add_argument("--max-edges", type=int, default=131072, help="fail-closed total edge cap")
    ingest_parser.add_argument("--max-refs", type=int, default=65536, help="fail-closed total unresolved-ref cap")
    ingest_parser.add_argument(
        "--max-file-bytes", type=int, default=4 * 1024 * 1024, help="per-file size cap (default 4 MiB)"
    )
    ingest_parser.add_argument("--json", action="store_true", help="print the canonical JSON report")

    # Contract v3 (addendum docs/contracts/ingestion-contract-v3.md; audit
    # F2): the frozen adapter mapping names ``ingest-file`` /
    # ``ingest-directory`` as the CLI surface of the two ingestion verbs.
    # They run the same shared adapter helper as the HTTP/MCP surfaces
    # (contract input validation, path discipline against the fixed
    # ingestion root — the process working directory for the CLI — and the
    # output budget shaping) and print the canonical JSON proposal payload.
    # The ops ``ingest`` command above is untouched.
    ingest_file_parser = subparsers.add_parser(
        "ingest-file",
        help="knowledge.ingest_file: ingest one file under the working directory (contract v3, proposal-only)",
    )
    ingest_file_parser.add_argument("path", help="file to ingest (resolved against the working directory)")
    ingest_file_parser.add_argument(
        "--token-budget", type=int, default=1024, help="response token budget, [128, max] (default 1024)"
    )
    ingest_directory_parser = subparsers.add_parser(
        "ingest-directory",
        help="knowledge.ingest_directory: ingest a directory under the working directory (contract v3, proposal-only)",
    )
    ingest_directory_parser.add_argument("path", help="directory to ingest (resolved against the working directory)")
    ingest_directory_parser.add_argument(
        "--recursive",
        dest="recursive",
        action="store_true",
        default=True,
        help="walk subdirectories (default)",
    )
    ingest_directory_parser.add_argument(
        "--no-recursive", dest="recursive", action="store_false", help="only the top level"
    )
    ingest_directory_parser.add_argument(
        "--max-files", type=int, default=1024, help="walk cap, [1, 65536] (default 1024)"
    )
    ingest_directory_parser.add_argument(
        "--token-budget", type=int, default=1024, help="response token budget, [128, max] (default 1024)"
    )

    # Contract v4 (addendum docs/contracts/extraction-contract-v4.md): the
    # frozen adapter mapping names ``submit-extraction`` as the CLI surface of
    # the external-agent extraction verb. It runs the same shared adapter
    # helper as the HTTP/MCP surfaces (extraction root = the process working
    # directory, fixed at process startup) and prints the canonical JSON
    # receipt. The payload is a JSON object (or '-' for stdin) — the same
    # convention as the fourteen frozen operations.
    submit_extraction_parser = subparsers.add_parser(
        "submit-extraction",
        help="knowledge.submit_extraction: submit an external extraction (disk-mode; MANUAL_GATES=1 queues it)",
    )
    submit_extraction_parser.add_argument(
        "payload",
        nargs="?",
        default=None,
        help="JSON payload object; '-' reads it from stdin; omitted means {}",
    )

    # The contract-v4 review surface: operator-only (never an MCP tool).
    extraction_parser = subparsers.add_parser(
        "extraction",
        help="contract v4 review surface: review/approve/reject queued external extractions (operator-only)",
    )
    extraction_sub = extraction_parser.add_subparsers(dest="extraction_action", required=True)
    extraction_review = extraction_sub.add_parser("review", help="list the review queue, or show one submission's diff")
    extraction_review.add_argument(
        "--status", default=None, help="filter by status (pending_review, corroborated, ...)"
    )
    extraction_review.add_argument("--receipt", default=None, help="show one submission's proposed diff")
    for action in ("approve", "reject"):
        sub = extraction_sub.add_parser(action, help=f"{action} one queued extraction (operator-gated)")
        sub.add_argument("receipt_id", help="the ext-… receipt id")
        sub.add_argument("--note", default="", help="operator note recorded with the decision")

    # W14 I2: verifiable provenance chain. Read-only: re-derives the five
    # hash-chained links (source sha256 -> payload hash -> capability actor ->
    # submission event hash -> node event hash) for one node and reports the
    # exact broken link. Exit 0 verified, 1 failed/unknown node, 3 unverified
    # (legacy node without a contract-v4 provenance block — never faked).
    provenance_parser = subparsers.add_parser(
        "provenance",
        help="verify the 5-link extraction provenance chain of a node (read-only)",
    )
    provenance_sub = provenance_parser.add_subparsers(dest="provenance_action", required=True)
    provenance_verify = provenance_sub.add_parser("verify", help="verify one node's provenance chain")
    provenance_verify.add_argument("node_id", help="the graph node id to verify")
    provenance_verify.add_argument(
        "--json", action="store_true", dest="as_json", help="print the canonical JSON report"
    )
    provenance_verify.add_argument(
        "--root",
        default=None,
        help="extraction root: additionally re-hash the bound source file (link 0)",
    )

    # Wave-4 SUB-92: polling watcher over the ingestion pipeline. OPS surface
    # only — same rules as `ingest`: never a knowledge op, never MCP/HTTP.
    watch_parser = subparsers.add_parser(
        "watch",
        help="ops: poll a directory and incrementally ingest changes (proposal-only, not a knowledge op)",
    )
    watch_parser.add_argument("path", help="directory to watch (the ingestion root)")
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="seconds between polls (default 2.0; hard floor 0.05 — no busy-loop)",
    )
    watch_parser.add_argument("--once", action="store_true", help="run a single cycle and exit (tests/CI)")
    watch_parser.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles")
    watch_parser.add_argument(
        "--debounce", type=float, default=2.0, help="full debounce window in seconds for bursts (default 2.0)"
    )
    watch_parser.add_argument(
        "--quick-quiet",
        type=float,
        default=0.3,
        help="quiet window in seconds when at most --quick-max-changes files are pending (default 0.3)",
    )
    watch_parser.add_argument("--quick-max-changes", type=int, default=2, help="quick-path quiet threshold (default 2)")

    # W17 (SUB-138): node leases (ops surface; never a knowledge op).
    _add_lease_parser(subparsers)
    watch_parser.add_argument(
        "--full-scan-ceiling",
        type=int,
        default=500,
        help="above this many pending changes (or any removal) a full scan-diff replaces per-file ingest",
    )
    watch_parser.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="auto-degrade (log + stop, never crash) after this many consecutive cycle failures",
    )
    watch_parser.add_argument(
        "--force",
        action="store_true",
        help="override the WSL/remote-mount refusal (KNOW_YOUR_CONTEXT_NO_WATCH still wins)",
    )
    watch_parser.add_argument("--json", action="store_true", help="print the canonical JSON watch report")
    return parser


def _print_error(error: BaseException) -> None:
    print(canonical_json(error_envelope(error)), file=sys.stderr)


def _print_usage_error(message: str) -> None:
    """A CLI-usage failure envelope (exit 2); nothing reaches the store."""
    envelope = {"error": {"code": "CLI_USAGE", "message": message, "retryable": False, "audit_event_id": None}}
    print(canonical_json(envelope), file=sys.stderr)


def _ingest_verb_command(args: argparse.Namespace, command: str) -> int:
    """The contract-v3 ingestion verbs (``ingest-file``/``ingest-directory``, audit F2).

    The same shared adapter helper as the HTTP/MCP surfaces: contract input
    validation, path discipline against the fixed ingestion root (the process
    working directory — fixed at process startup) and the output budget
    shaping; the canonical JSON proposal goes to stdout, typed failures use
    the standard envelope on stderr. Exit 0 success, 1 typed failure.
    """
    from know_your_context.audit import AuditLog
    from know_your_context.ingest.pipeline import ExtractorUnavailableError, IngestionError
    from know_your_context.store import EventStore

    arguments: dict[str, Any] = {"path": args.path, "token_budget": args.token_budget}
    if command == "ingest_directory":
        arguments["recursive"] = args.recursive
        arguments["max_files"] = args.max_files
    store = EventStore(args.store)
    try:
        payload = run_ingest_verb(
            store,
            AuditLog(store),
            command,
            arguments,
            actor=args.actor,
            ingestion_root=os.getcwd(),
        )
    except (IngestionError, ExtractorUnavailableError) as error:
        envelope = {
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": False,
                "audit_event_id": getattr(error, "audit_event_id", None),
            }
        }
        print(canonical_json(envelope), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - every failure uses the envelope
        _print_error(error)
        return 1
    finally:
        store.close()
    print(canonical_json(payload))
    return 0


def _extraction_verb_command(args: argparse.Namespace) -> int:
    """The contract-v4 ``submit-extraction`` verb (addendum extraction-contract-v4.md).

    The same shared adapter helper as the HTTP/MCP surfaces: contract input
    validation, source binding against the fixed extraction root (the process
    working directory — fixed at process startup), and the canonical JSON
    receipt on stdout. Exit 0 success, 1 typed failure, 2 adapter rejection.
    """
    import json as _json

    from know_your_context.extraction import ExtractionError

    payload_text: str = args.payload if args.payload is not None else "{}"
    if payload_text == "-":
        payload_text = sys.stdin.read()
    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        try:
            arguments = _json.loads(payload_text)
        except _json.JSONDecodeError as error:
            _print_usage_error(f"payload is not valid JSON: {error}")
            return 2
        payload = run_extraction_verb(
            stack.store,
            stack.audit,
            "submit_extraction",
            arguments,
            actor=args.actor,
            extraction_root=os.getcwd(),
            service=stack.service,
        )
    except ExtractionError as error:
        envelope = {
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": False,
                "audit_event_id": getattr(error, "audit_event_id", None),
            }
        }
        print(canonical_json(envelope), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - every failure uses the envelope
        _print_error(error)
        return 1
    finally:
        stack.close()
    print(canonical_json(payload))
    return 0


def _extraction_review_command(args: argparse.Namespace) -> int:
    """The contract-v4 operator review surface: ``extraction review|approve|reject``.

    Read-only listing/diff for ``review``; ``approve`` applies the queued
    proposal through the existing audited write path; ``reject`` marks the
    row. Exit 0 success, 1 typed failure.
    """
    from know_your_context.extraction import ExtractionEngine, ExtractionError, ExtractionQueue

    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        engine = ExtractionEngine(stack.store, stack.audit, queue=ExtractionQueue.for_store(stack.store))
        try:
            if args.extraction_action == "review":
                payload = engine.review(status=args.status, receipt_id=args.receipt)
            elif args.extraction_action == "approve":
                payload = engine.approve(args.receipt_id, operator=args.actor, service=stack.service, note=args.note)
            else:
                payload = engine.reject(args.receipt_id, operator=args.actor, note=args.note)
        except ExtractionError as error:
            envelope = {
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "retryable": False,
                    "audit_event_id": getattr(error, "audit_event_id", None),
                }
            }
            print(canonical_json(envelope), file=sys.stderr)
            return 1
    finally:
        stack.close()
    print(canonical_json(payload))
    return 0


def _config_command(args: argparse.Namespace) -> int:
    """The ``config`` OPS command: layered configuration introspection (SUB-77/SUB-80).

    Never touches the store. The CLI's own explicit flags join the resolution
    as the top ``cli`` layer so the resolved view matches the invocation.
    """
    from know_your_context.ops import config as ops_config

    cli_overrides: dict[str, Any] = {}
    if args.store != _default_store_path():
        cli_overrides["store.path"] = args.store
    if args.output_root != DEFAULT_OUTPUT_ROOT:
        cli_overrides["projection.output_root"] = args.output_root
    if args.actor != "operator":
        cli_overrides["ops.actor"] = args.actor
    if args.ops_root is not None:
        cli_overrides["ops.root"] = args.ops_root
    try:
        config = ops_config.load_config(file_path=args.config, cli_overrides=cli_overrides)
    except ops_config.ConfigError as error:
        _print_usage_error(str(error))
        return 2
    if args.config_command == "describe":
        print(ops_config.describe_config(config))
    elif args.config_command == "dump":
        print(canonical_json(config.dump()))
    else:  # effective
        print(canonical_json(config.effective()))
    return 0


def _metrics_command(args: argparse.Namespace) -> int:
    """The ``metrics`` OPS command: read-only operational metrics (SUB-77/SUB-80)."""
    from know_your_context.ops import metrics as ops_metrics

    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        collected = ops_metrics.collect_ops_metrics(
            stack.store, projection_root=args.output_root, stale_after_seconds=args.stale_after
        )
    finally:
        stack.close()
    if args.format == "markdown":
        print(ops_metrics.render_markdown(collected))
    else:
        print(canonical_json(to_jsonable(collected)))
    return 0


def _diagnose_command(args: argparse.Namespace) -> int:
    """The ``diagnose`` OPS command: read-only self-check, exit code from the report (SUB-77/SUB-80)."""
    from know_your_context.ops import diagnose as ops_diagnose

    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        report = ops_diagnose.run_diagnostics(
            stack.store,
            projection_root=args.output_root,
            stale_after_seconds=args.stale_after,
            check_permissions=not args.no_permissions,
        )
    finally:
        stack.close()
    if args.format == "markdown":
        print(ops_diagnose.render_markdown(report))
    else:
        payload = {
            "ok": report.ok,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "worst_severity": report.worst_severity,
            "checked": list(report.checked),
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "details": finding.details,
                }
                for finding in report.findings
            ],
        }
        print(canonical_json(to_jsonable(payload)))
    return ops_diagnose.exit_code(report)


def _lifecycle_command(args: argparse.Namespace) -> int:
    """W12 (SUB-122): ``lifecycle record|trend|prune|vacuum|fragmentation``."""
    from know_your_context.ops import lifecycle as ops_lifecycle
    from know_your_context.store import EventStore

    action = str(args.lifecycle_action)
    try:
        if action in {"record", "trend", "prune"}:
            history_path = (
                Path(args.history)
                if getattr(args, "history", None)
                else Path(args.store).resolve().parent / ops_lifecycle.DEFAULT_HISTORY_FILENAME
            )
            if action == "record":
                store = EventStore(args.store)
                try:
                    snapshot = ops_lifecycle.record_health_snapshot(
                        store, history_path=history_path, projection_root=args.output_root
                    )
                finally:
                    store.close()
                print(canonical_json(to_jsonable(snapshot.to_payload())))
                return 0
            history = ops_lifecycle.load_health_history(history_path)
            if action == "trend":
                trend = ops_lifecycle.analyze_health_trend(history)
                print(canonical_json(to_jsonable(trend)))
                return 0
            report = ops_lifecycle.prune_health_history(history_path, keep=int(args.keep), dry_run=bool(args.dry_run))
            print(canonical_json(to_jsonable(report)))
            return 0
        store = EventStore(args.store)
        try:
            if action == "vacuum":
                report = ops_lifecycle.vacuum_store(store)
                print(canonical_json(to_jsonable(report)))
                return 0
            report = ops_lifecycle.analyze_fragmentation(store)
            print(canonical_json(to_jsonable(report)))
            return 0
        finally:
            store.close()
    except ops_lifecycle.LifecycleError as error:
        _print_error(error)
        return 1


def _doctor_command(args: argparse.Namespace) -> int:
    """W12 (SUB-122): ``doctor`` deep diagnostic; exit code from the report."""
    from know_your_context.ops import doctor as ops_doctor
    from know_your_context.store import EventStore

    store = EventStore(args.store)
    try:
        report = ops_doctor.run_doctor(
            store,
            full_integrity=bool(args.full_integrity),
            stale_after_seconds=float(args.stale_after),
        )
    finally:
        store.close()
    if args.format == "markdown":
        print(ops_doctor.render_markdown(report))
    else:
        payload = {
            "ok": report.ok,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "worst_severity": report.worst_severity,
            "category_counts": report.category_counts,
            "checked": list(report.checked),
            "findings": [
                {
                    "code": finding.code,
                    "category": finding.category,
                    "severity": finding.severity,
                    "message": finding.message,
                    "suggestions": list(finding.suggestions),
                    "details": finding.details,
                }
                for finding in report.findings
            ],
        }
        print(canonical_json(to_jsonable(payload)))
    return ops_doctor.exit_code(report)


def _migrate_command(args: argparse.Namespace) -> int:
    """W12 (SUB-122): ``migrate plan|apply|export|import|verify``."""
    from know_your_context.backup import BackupError
    from know_your_context.ops import migrate as ops_migrate
    from know_your_context.store import EventStore

    action = str(args.migrate_action)
    try:
        if action == "plan":
            plan = ops_migrate.plan_migration(args.store)
            print(canonical_json(to_jsonable(plan)))
            return 0
        if action == "apply":
            report = ops_migrate.migrate_store(args.store)
            print(canonical_json(to_jsonable(report)))
            return 0
        if action == "export":
            store = EventStore(args.store)
            try:
                manifest = ops_migrate.export_bundle(store, args.dest)
            finally:
                store.close()
            print(canonical_json(to_jsonable(manifest.to_payload())))
            return 0
        if action == "import":
            dest = args.dest if args.dest is not None else args.store
            report = ops_migrate.import_bundle(args.bundle, dest, overwrite=bool(args.overwrite))
            print(canonical_json(to_jsonable(report)))
            return 0
        verification = ops_migrate.verify_bundle(args.bundle)
        payload: dict[str, Any] = {
            "ok": verification.ok,
            "errors": list(verification.errors),
            "manifest": verification.manifest.to_payload() if verification.manifest is not None else None,
        }
        print(canonical_json(to_jsonable(payload)))
        return 0 if verification.ok else 1
    except (ops_migrate.MigrateError, BackupError) as error:
        _print_error(error)
        return 1


def _watchtower_command(args: argparse.Namespace) -> int:
    """W12 (SUB-122): ``watchtower`` multi-store status aggregation."""
    from know_your_context.ops import watchtower as ops_watchtower

    targets: list[ops_watchtower.StoreTarget] = []
    for raw in args.target or []:
        if "=" not in raw:
            _print_usage_error(f"--target must be NAME=PATH, got {raw!r}")
            return 2
        name, _, path = raw.partition("=")
        targets.append(ops_watchtower.StoreTarget(name=name.strip(), store_path=path.strip()))
    for positional in args.stores:
        path = Path(positional)
        targets.append(ops_watchtower.StoreTarget(name=path.stem, store_path=str(path)))
    try:
        report = ops_watchtower.aggregate_status(tuple(targets), stale_after_seconds=float(args.stale_after))
    except ops_watchtower.WatchtowerError as error:
        _print_error(error)
        return 1
    if args.format == "markdown":
        print(ops_watchtower.render_markdown(report))
    else:
        print(canonical_json(to_jsonable(report)))
    return ops_watchtower.exit_code(report)


def _serve_superpower_command(args: argparse.Namespace) -> int:
    """Wave-7 SUB-100: detached superpower daemon + thin MCP stdio proxy."""
    import json as _json

    from know_your_context.serve.daemon_config import ConfigError, DaemonConfig
    from know_your_context.serve.superpower import DaemonUnavailable, run_daemon, spawn_daemon

    try:
        kwargs: dict[str, Any] = {
            "db_path": args.store,
            "graph_root": args.output_root,
            "idle_timeout_seconds": args.idle_timeout,
            "capability_mode": args.capability_mode,
            "node_leases_enabled": bool(args.node_leases),
        }
        if args.node_lease_ttl is not None:
            kwargs["node_lease_ttl_seconds"] = float(args.node_lease_ttl)
        # None = the operator did not ask; the hardware plan derives it
        # (scale-up only from the historical default).
        if args.max_agents is not None:
            kwargs["max_agents"] = args.max_agents
        if args.read_pool_size is not None:
            kwargs["read_pool_size"] = args.read_pool_size
        config = DaemonConfig(**kwargs)
    except ConfigError as error:
        _print_usage_error(f"invalid superpower configuration: {error}")
        return 2
    if args.daemon_foreground:
        return run_daemon(config)
    if args.proxy:
        from know_your_context.serve.proxy import run_proxy

        return run_proxy(config, actor=args.actor)
    if os.environ.get("KNOW_YOUR_CONTEXT_DAEMON_INTERNAL"):
        print("know-your-context serve --daemon: refusing recursive daemon spawn", file=sys.stderr)
        return 2
    try:
        result = spawn_daemon(config)
    except DaemonUnavailable as error:
        print(f"know-your-context serve --daemon: {error}", file=sys.stderr)
        return 1
    status = "already_running" if result.already_running else "started"
    print(_json.dumps({"status": status, "pid": result.pid, "socket": str(result.socket_path)}, sort_keys=True))
    return 0


def _daemon_command(args: argparse.Namespace) -> int:
    """Wave-7 SUB-100: `daemon status|stop` against the store's daemon."""
    import json as _json

    from know_your_context.serve.daemon_config import ConfigError, DaemonConfig
    from know_your_context.serve.superpower import DaemonUnavailable, daemon_status, stop_daemon

    try:
        config = DaemonConfig(db_path=args.store, graph_root=args.output_root)
    except ConfigError as error:
        _print_usage_error(f"invalid superpower configuration: {error}")
        return 2
    if args.daemon_action == "status":
        print(_json.dumps(daemon_status(config), indent=2, sort_keys=True))
        return 0
    try:
        # W10: under capability enforcement the graceful shutdown needs an
        # admin token; the operator holds the local secret (root of trust),
        # so one is minted on the fly. Without a secret the daemon's own
        # escalation ladder (drain -> force -> SIGKILL) still stops it.
        from know_your_context.security.capabilities import CapabilityStore, mint_local_admin_token

        capability = mint_local_admin_token(CapabilityStore(config.paths.root), actor=args.actor)
        print(_json.dumps(stop_daemon(config, force=args.force, capability=capability), indent=2, sort_keys=True))
    except DaemonUnavailable as error:
        print(f"know-your-context daemon stop: {error}", file=sys.stderr)
        return 1
    return 0


def _capability_command(args: argparse.Namespace) -> int:
    """W10 (SUB-117): `capability issue|revoke|list` against the daemon store."""
    import json as _json

    from know_your_context.security.capabilities import (
        CapabilityError,
        CapabilityStore,
        CapabilityStoreError,
    )
    from know_your_context.serve.daemon_config import ConfigError, DaemonConfig

    try:
        config = DaemonConfig(db_path=args.store, graph_root=args.output_root)
    except ConfigError as error:
        _print_usage_error(f"invalid capability configuration: {error}")
        return 2
    store = CapabilityStore(config.paths.root)
    action = str(args.capability_action)
    try:
        if action == "issue":
            config.paths.ensure()
            store.ensure_secret()
            verbs = (
                [verb.strip() for verb in str(args.verbs).split(",") if verb.strip()]
                if args.verbs is not None
                else None
            )
            token, capability = store.issue(
                actor=str(args.actor),
                scope=str(args.scope),
                verbs=verbs,
                ttl_seconds=float(args.ttl_seconds),
                expires_at=float(args.expires_at) if args.expires_at is not None else None,
            )
            print(_json.dumps({"token": token, "capability": capability.record()}, indent=2, sort_keys=True))
            return 0
        if action == "revoke":
            store.load_secret()  # fail closed when no capability store exists
            if args.nonce is not None:
                report = store.revoke(str(args.nonce))
            else:
                report = store.revoke_token(str(args.token))
            print(_json.dumps(report, indent=2, sort_keys=True))
            return 0
        store.load_secret()  # list: the store must exist
        print(_json.dumps(store.list_registry(), indent=2, sort_keys=True))
        return 0
    except CapabilityStoreError as error:
        print(f"know-your-context capability {action}: {error}", file=sys.stderr)
        return 1
    except CapabilityError as error:
        _print_usage_error(str(error))
        return 2


def _serve_command(args: argparse.Namespace) -> int:
    if args.daemon or args.proxy or args.daemon_foreground:
        return _serve_superpower_command(args)
    socket_path: str | None = args.socket
    http: str | None = args.http
    if socket_path is None and http is None:
        socket_path = DEFAULT_SOCKET_PATH
    # Wave-2 F6: the key can come from the environment so it never sits in
    # argv; a named-but-unset variable fails closed rather than serving
    # unauthenticated.
    api_key: str | None = args.api_key
    if args.api_key_env is not None:
        api_key = os.environ.get(args.api_key_env) or None
        if api_key is None:
            _print_usage_error(f"environment variable {args.api_key_env} is not set or is empty")
            return 2
    allowlist = (
        tuple(entry.strip() for entry in args.host_allowlist.split(",") if entry.strip())
        if args.host_allowlist
        else DEFAULT_HOST_ALLOWLIST
    )
    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        kwargs: dict[str, Any] = {"api_key": api_key, "host_allowlist": allowlist}
        if args.max_body_bytes is not None:
            kwargs["max_body_bytes"] = args.max_body_bytes
        if args.watch is not None:
            # Arch review F-1: the spec-13.5 doc-change watcher rides the
            # server's lifespan; detect-only, offers gated on the operator.
            kwargs["watch_root"] = args.watch
            kwargs["watch_interval_seconds"] = args.watch_interval
            kwargs["watch_cooldown_seconds"] = args.watch_cooldown
            if args.watch_state is not None:
                kwargs["watch_state_path"] = args.watch_state
        serve(stack, socket_path=socket_path, http=http, **kwargs)
    except (BindPolicyError, UpdatePipelineError) as error:
        _print_error(error)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        stack.close()
    return 0


def _provenance_command(args: argparse.Namespace) -> int:
    """The W14 I2 ``provenance verify`` command (read-only).

    Exit 0 = verified (all five links re-derive and match), 1 = failed (a
    typed broken link, reported with its index) or unknown node, 3 =
    unverified (a legacy node with no contract-v4 provenance block — the
    honest status, never faked as verified).
    """
    from know_your_context.security.provenance import ProvenanceNodeNotFoundError, verify_provenance
    from know_your_context.store import EventStore

    store = EventStore(args.store)
    try:
        try:
            report = verify_provenance(store, args.node_id, extraction_root=args.root)
        except ProvenanceNodeNotFoundError as error:
            envelope = {
                "error": {
                    "code": "PROVENANCE_NODE_NOT_FOUND",
                    "message": str(error),
                    "retryable": False,
                    "audit_event_id": None,
                }
            }
            print(canonical_json(envelope), file=sys.stderr)
            return 1
    finally:
        store.close()
    if args.as_json:
        print(canonical_json(report.to_dict()))
    else:
        print(f"provenance {report.node_id}: {report.status}")
        for link in report.links:
            suffix = f" [{link.error_type}]" if link.error_type else ""
            print(f"  [{link.index}] {link.name:<18} {link.status:<10} {link.detail}{suffix}")
        print(f"  reason: {report.reason}")
    if report.status == "verified":
        return 0
    if report.status == "unverified":
        return 3
    return 1


def _lease_command(args: argparse.Namespace) -> int:
    """The ``lease`` OPS command (W17 SUB-138): node lease CRUD over --store.

    Never a knowledge operation; every mutation is recorded in the
    hash-chained event log by the registry itself.
    """
    from know_your_context.leases import DEFAULT_LEASE_TTL_SECONDS, LeaseError, LeaseRegistry
    from know_your_context.store import EventStore

    store_path = Path(str(args.store))
    if not store_path.is_file():
        _print_usage_error(f"lease: store not found or not a file: {store_path}")
        return 2
    store = EventStore(store_path)
    try:
        registry = LeaseRegistry(store)
        action = str(args.lease_action)
        if action == "list":
            leases = registry.active_leases()
            print(canonical_json({"count": len(leases), "leases": [lease.to_dict() for lease in leases]}))
            return 0
        if action == "acquire":
            ttl = args.ttl if args.ttl is not None else DEFAULT_LEASE_TTL_SECONDS
            lease = registry.acquire(str(args.node_id), str(args.actor), ttl_seconds=float(ttl))
            print(canonical_json(lease.to_dict()))
            return 0
        if action == "release":
            released = registry.release(str(args.node_id), str(args.actor))
            print(canonical_json({"node_id": str(args.node_id), "released": released}))
            return 0
        # force-release
        released = registry.force_release(str(args.node_id), actor=str(args.actor or "operator"))
        print(canonical_json({"node_id": str(args.node_id), "released": released}))
        return 0
    except LeaseError as error:
        envelope = {
            "error": {
                "code": getattr(error, "code", "LEASE_ERROR"),
                "message": str(error),
                "retryable": getattr(error, "code", "") == "NODE_IN_USE",
                "audit_event_id": None,
            }
        }
        print(canonical_json(envelope), file=sys.stderr)
        return 1
    finally:
        store.close()


def _webview_serve(store: str, port: int, *, open_browser: bool, serve_once: bool) -> int:
    """Delegate to the webview server entry point (seam for tests)."""
    from know_your_context.serve.webview import main as webview_main

    webview_argv = ["--store", store, "--port", str(port)]
    if open_browser:
        webview_argv.append("--open")
    if serve_once:
        webview_argv.append("--serve-once")
    return webview_main(webview_argv)


def _webview_command(args: argparse.Namespace) -> int:
    """The ``webview`` OPS command (W17): lifecycle on/off/status + open.

    The preference persists next to the store; ``status`` reconciles the file
    with a live TCP probe of the saved port (never trusts the file alone).
    """
    import socket as _socket

    from know_your_context.ops.webview_control import WebviewPrefsError, read_prefs, write_prefs

    store = str(args.store)
    action = getattr(args, "webview_action", None)
    if action in ("status", "on") and not Path(store).is_file():
        _print_usage_error(f"webview: store not found or not a file: {store}")
        return 2
    try:
        prefs = read_prefs(store)
    except WebviewPrefsError as error:
        _print_usage_error(f"webview: {error}")
        return 2

    if action == "status":
        running = False
        port = prefs["port"]
        if port is not None:
            try:
                with _socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                    running = True
            except OSError:
                running = False
        print(
            canonical_json(
                {
                    "enabled": prefs["enabled"],
                    "port": prefs["port"],
                    "running": running,
                    "updated_at": prefs["updated_at"],
                }
            )
        )
        return 0
    if action == "off":
        write_prefs(store, enabled=False)
        print(canonical_json({"status": "disabled"}))
        return 0
    if action == "on":
        write_prefs(store, enabled=True, port=int(args.port) or None)
        print(canonical_json({"status": "enabled"}))
        return _webview_serve(store, int(args.port), open_browser=not args.no_open, serve_once=bool(args.serve_once))
    # Bare `webview`: follow the persisted preference.
    if prefs["enabled"] is False:
        print(canonical_json({"status": "disabled", "hint": "run 'know-your-context webview on' to re-enable"}))
        return 0
    port = int(args.port) or (prefs["port"] or 0)
    return _webview_serve(store, port, open_browser=not args.no_open, serve_once=bool(args.serve_once))


def _setup_command(args: argparse.Namespace) -> int:
    """The ``setup`` OPS command (W17): install flow with a live webview."""
    from know_your_context.ops.setup_flow import run_setup

    report = run_setup(
        str(args.store),
        port=int(args.port),
        serve_webview=bool(args.serve_webview),
        hold=bool(args.hold),
    )
    if report.get("url"):
        print(f"webview: {report['url']}", flush=True)
    print(canonical_json(to_jsonable(report)))
    return 0 if report["state"] == "done" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = str(args.command).replace("-", "_")
    if command == "serve":
        return _serve_command(args)
    if command == "daemon":
        return _daemon_command(args)
    if command == "capability":
        return _capability_command(args)
    if command in ("export", "analytics"):
        from know_your_context.ops.export_cli import run_export_command

        return run_export_command(args, command=command)
    if command == "report":
        from know_your_context.ops.report_cli import run_report_command

        return run_report_command(args)
    if command == "debt":
        from know_your_context.ops.debt import run_debt_command

        return run_debt_command(args)
    if command == "codeintel":
        from know_your_context.ops.codeintel_cli import run_codeintel_command

        return run_codeintel_command(args)
    if command == "config":
        return _config_command(args)
    if command == "metrics":
        return _metrics_command(args)
    if command == "diagnose":
        return _diagnose_command(args)
    if command == "lifecycle":
        return _lifecycle_command(args)
    if command == "doctor":
        return _doctor_command(args)
    if command == "migrate":
        return _migrate_command(args)
    if command == "watchtower":
        return _watchtower_command(args)
    if command == "webview":
        return _webview_command(args)
    if command == "setup":
        return _setup_command(args)
    if command == "ingest":
        from know_your_context.ops.ingest_cli import run_ingest_command

        return run_ingest_command(args)
    if command in ("ingest_file", "ingest_directory"):
        return _ingest_verb_command(args, command)
    if command == "submit_extraction":
        return _extraction_verb_command(args)
    if command == "extraction":
        return _extraction_review_command(args)
    if command == "provenance":
        return _provenance_command(args)
    if command == "watch":
        from know_your_context.ops.watch_cli import run_watch_command

        return run_watch_command(args)
    if command == "lease":
        return _lease_command(args)

    payload_text: str = args.payload if args.payload is not None else "{}"
    if payload_text == "-":
        payload_text = sys.stdin.read()

    stack = build_stack(args.store, args.output_root, ops_root=args.ops_root)
    try:
        result = dispatch_text(stack, command, payload_text, actor=args.actor, idempotency_key=args.idempotency_key)
    except AdapterValidationError as error:
        _print_error(error)
        return 2
    except Exception as error:  # noqa: BLE001 - every failure uses the envelope
        _print_error(error)
        return 1
    finally:
        stack.close()
    payload = to_jsonable(result)
    print(canonical_json(payload))
    if command == "health" and not payload.get("ok"):
        return 1
    return 0
