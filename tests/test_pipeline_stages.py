"""Tests for serve/pipeline_stages.py — explicit staged pipeline (W13).

Stages: extract → resolve → synthesize → index → seal, with per-stage
contracts, checkpoints, resume-from-last-good-stage, per-stage metrics, and
independent retry. Generic-runner tests use scripted stages; the ingestion
tests run the real pipeline/resolver/synthesis/FTS stack over a real store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from _ingest_fakes import ToyExtractor, ToyResolver, write_fixture
from know_your_context.ingest.pipeline import IngestionPipeline
from know_your_context.serve.flow_retry import RetryPolicy
from know_your_context.serve.pipeline_stages import (
    STAGE_ORDER,
    CheckpointCorruptError,
    CheckpointStore,
    StageContractError,
    StagedPipelineRunner,
    StageFailedError,
    StageOutput,
    build_ingestion_stages,
    verify_seal_chain,
)
from know_your_context.store import EventStore


class _Stage:
    """Scripted stage: fails ``failures`` times, then emits fixed output."""

    def __init__(
        self,
        name: str,
        *,
        output: dict[str, Any] | None = None,
        items_in: int = 0,
        items_out: int = 0,
        failures: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self._output = output if output is not None else {"stage": name}
        self._items_in = items_in
        self._items_out = items_out
        self._failures_left = failures
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def run(self, context: Any) -> StageOutput:
        self.calls += 1
        if self._failures_left > 0:
            self._failures_left -= 1
            raise self._error if self._error is not None else OSError(f"{self._name} transient")
        return StageOutput(output=dict(self._output), items_in=self._items_in, items_out=self._items_out)


def _runner(
    tmp_path: Path,
    stages: list[_Stage],
    *,
    payload: dict[str, Any] | None = None,
    policy: RetryPolicy | None = None,
    clock: Any = None,
) -> StagedPipelineRunner:
    ticks = iter([1000.0 + i * 0.01 for i in range(10_000)])
    return StagedPipelineRunner(
        stages,
        checkpoints=CheckpointStore(tmp_path / "checkpoints"),
        input_payload=payload if payload is not None else {"root": "/repo", "mode": "full"},
        retry_policy=policy if policy is not None else RetryPolicy(max_attempts=1, jitter_ratio=0.0),
        now=clock if clock is not None else lambda: next(ticks),
        sleep=lambda _: None,
    )


# ------------------------------------------------------------ checkpoint store


class TestCheckpointStore:
    def test_missing_file_loads_empty(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        assert store.load_stages() == {}
        assert store.load_input_digest() is None

    def test_roundtrip(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        store.save_stage("extract", status="ok", output_digest="a" * 64, metrics={"attempts": 1})
        store.set_input_digest("b" * 64)
        stages = store.load_stages()
        assert stages["extract"]["status"] == "ok"
        assert stages["extract"]["output_digest"] == "a" * 64
        assert store.load_input_digest() == "b" * 64

    def test_second_save_preserves_siblings(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        store.save_stage("extract", status="ok", output_digest="a" * 64, metrics={})
        store.save_stage("resolve", status="failed", output_digest=None, metrics={}, error="boom")
        stages = store.load_stages()
        assert set(stages) == {"extract", "resolve"}
        assert stages["resolve"]["error"] == "boom"

    def test_tamper_detected(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        store.save_stage("extract", status="ok", output_digest="a" * 64, metrics={})
        path = store.file_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["stages"]["extract"]["status"] = "failed"  # silent edit
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(CheckpointCorruptError):
            store.load_stages()

    def test_invalid_json_detected(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        store.file_path.parent.mkdir(parents=True, exist_ok=True)
        store.file_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(CheckpointCorruptError):
            store.load_stages()

    def test_clear(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        store.save_stage("extract", status="ok", output_digest="a" * 64, metrics={})
        store.clear()
        assert store.load_stages() == {}
        assert not store.file_path.exists()

    def test_invalidate_downstream(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        for name in ("extract", "resolve", "synthesize"):
            store.save_stage(name, status="ok", output_digest="a" * 64, metrics={})
        store.invalidate_from("resolve", order=["extract", "resolve", "synthesize"])
        assert set(store.load_stages()) == {"extract"}

    def test_status_value_rejected(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "cp")
        with pytest.raises(ValueError, match="status"):
            store.save_stage("extract", status="maybe", output_digest=None, metrics={})


# ------------------------------------------------------------- generic runner


class TestRunnerHappyPath:
    def test_runs_all_stages_in_order(self, tmp_path: Path) -> None:
        stages = [_Stage(name, items_out=i) for i, name in enumerate(STAGE_ORDER)]
        report = _runner(tmp_path, stages).run()
        assert report.status == "ok"
        assert [r.stage for r in report.stages] == list(STAGE_ORDER)
        assert all(r.status == "ok" for r in report.stages)
        assert all(r.attempts == 1 for r in report.stages)
        assert [r.items_out for r in report.stages] == [0, 1, 2, 3, 4]
        assert all(s.calls == 1 for s in stages)

    def test_report_has_deterministic_run_id(self, tmp_path: Path) -> None:
        stages = [_Stage(name) for name in STAGE_ORDER]
        r1 = _runner(tmp_path / "a", stages).run().run_id
        stages2 = [_Stage(name) for name in STAGE_ORDER]
        r2 = _runner(tmp_path / "b", stages2).run().run_id
        assert r1 == r2  # run_id is a pure function of the input payload

    def test_input_digest_changes_with_payload(self, tmp_path: Path) -> None:
        a = _runner(tmp_path / "a", [_Stage("extract")], payload={"root": "/a"}).run()
        b = _runner(tmp_path / "b", [_Stage("extract")], payload={"root": "/b"}).run()
        assert a.input_digest != b.input_digest

    def test_report_to_dict(self, tmp_path: Path) -> None:
        report = _runner(tmp_path, [_Stage("extract")]).run()
        payload = report.to_dict()
        assert payload["status"] == "ok"
        assert payload["stages"][0]["stage"] == "extract"
        json.dumps(payload)  # serializable

    def test_duration_from_injected_clock(self, tmp_path: Path) -> None:
        report = _runner(tmp_path, [_Stage("extract")]).run()
        assert all(r.duration_ms >= 0 for r in report.stages)


class TestRunnerFailureAndResume:
    def test_failure_marks_checkpoint_and_raises(self, tmp_path: Path) -> None:
        extract, resolve, synth = _Stage("extract"), _Stage("resolve", failures=5), _Stage("synthesize")
        runner = _runner(tmp_path, [extract, resolve, synth])
        with pytest.raises(StageFailedError) as info:
            runner.run()
        assert info.value.stage == "resolve"
        stages = runner.checkpoints.load_stages()
        assert stages["extract"]["status"] == "ok"
        assert stages["resolve"]["status"] == "failed"
        assert "synthesize" not in stages  # never reached

    def test_resume_skips_completed_stages(self, tmp_path: Path) -> None:
        extract, resolve, synth = _Stage("extract"), _Stage("resolve", failures=5), _Stage("synthesize")
        runner = _runner(tmp_path, [extract, resolve, synth])
        with pytest.raises(StageFailedError):
            runner.run()
        resolve._failures_left = 0  # operator fixed the world
        report = runner.run(resume=True)
        assert report.status == "ok"
        assert extract.calls == 1  # NOT re-run
        assert resolve.calls == 2  # 1 failed attempt in run #1, 1 success in run #2 (policy max_attempts=1)
        statuses = {r.stage: r.status for r in report.stages}
        assert statuses["extract"] == "resumed"
        assert statuses["resolve"] == "ok"
        assert statuses["synthesize"] == "ok"

    def test_resume_with_changed_input_reruns_all(self, tmp_path: Path) -> None:
        stages = [_Stage("extract"), _Stage("resolve")]
        _runner(tmp_path, stages, payload={"root": "/a"}).run()
        stages2 = [_Stage("extract"), _Stage("resolve")]
        runner = _runner(tmp_path, stages2, payload={"root": "/B"})
        report = runner.run(resume=True)
        assert all(r.status == "ok" for r in report.stages)  # nothing resumed
        assert stages2[0].calls == 1

    def test_no_resume_reruns_everything(self, tmp_path: Path) -> None:
        stages = [_Stage("extract"), _Stage("resolve")]
        _runner(tmp_path, stages).run()
        stages2 = [_Stage("extract"), _Stage("resolve")]
        report = _runner(tmp_path, stages2).run(resume=False)
        assert all(r.status == "ok" for r in report.stages)
        assert stages2[0].calls == 1

    def test_resumed_run_keeps_run_id(self, tmp_path: Path) -> None:
        stages = [_Stage("extract"), _Stage("resolve", failures=5)]
        runner = _runner(tmp_path, stages)
        with pytest.raises(StageFailedError):
            runner.run()
        stages[1]._failures_left = 0
        resumed = runner.run(resume=True)
        original = runner.run(resume=True)
        assert resumed.run_id == original.run_id


class TestRunnerRetry:
    def test_stage_retries_transient_failures(self, tmp_path: Path) -> None:
        stage = _Stage("extract", failures=2)
        policy = RetryPolicy(max_attempts=3, jitter_ratio=0.0)
        report = _runner(tmp_path, [stage], policy=policy).run()
        assert report.status == "ok"
        assert stage.calls == 3
        record = report.stages[0]
        assert record.attempts == 3
        assert record.retries == 2

    def test_retry_exhaustion_is_stage_failure(self, tmp_path: Path) -> None:
        stage = _Stage("extract", failures=99)
        policy = RetryPolicy(max_attempts=3, jitter_ratio=0.0)
        with pytest.raises(StageFailedError) as info:
            _runner(tmp_path, [stage], policy=policy).run()
        assert info.value.attempts == 3
        assert stage.calls == 3

    def test_non_retryable_error_fails_fast(self, tmp_path: Path) -> None:
        stage = _Stage("extract", failures=5, error=ValueError("permanent"))
        policy = RetryPolicy(max_attempts=3, jitter_ratio=0.0)
        with pytest.raises(StageFailedError):
            _runner(tmp_path, [stage], policy=policy).run()
        assert stage.calls == 1  # ValueError is not in the retryable set

    def test_run_stage_independently(self, tmp_path: Path) -> None:
        stages = [_Stage(name) for name in STAGE_ORDER]
        runner = _runner(tmp_path, stages)
        runner.run()
        metrics = runner.run_stage("synthesize")
        assert metrics.attempts >= 1
        assert stages[2].calls == 2
        # Downstream checkpoints invalidated: index+seal must re-run.
        report = runner.run(resume=True)
        statuses = {r.stage: r.status for r in report.stages}
        assert statuses["extract"] == "resumed"
        assert statuses["synthesize"] == "resumed"
        assert statuses["index"] == "ok"
        assert statuses["seal"] == "ok"

    def test_run_stage_unknown_rejected(self, tmp_path: Path) -> None:
        runner = _runner(tmp_path, [_Stage("extract")])
        with pytest.raises(ValueError, match="unknown"):
            runner.run_stage("unknown")


class TestRunnerContract:
    def test_duplicate_stage_names_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            _runner(tmp_path, [_Stage("extract"), _Stage("extract")])

    def test_empty_stages_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="stage"):
            _runner(tmp_path, [])

    def test_non_serializable_output_is_contract_error(self, tmp_path: Path) -> None:
        stage = _Stage("extract", output={"bad": object()})
        with pytest.raises(StageContractError):
            _runner(tmp_path, [stage]).run()
        # A contract failure is not retried and leaves a failed checkpoint.
        assert stage.calls == 1
        stages = CheckpointStore(tmp_path / "checkpoints").load_stages()
        assert stages["extract"]["status"] == "failed"

    def test_stage_returning_wrong_type_is_contract_error(self, tmp_path: Path) -> None:
        class Bad:
            @property
            def name(self) -> str:
                return "extract"

            def run(self, context: Any) -> str:
                return "not-a-stage-output"

        with pytest.raises(StageContractError):
            _runner(tmp_path, [Bad()]).run()  # type: ignore[list-item]

    def test_stage_context_threads_data(self, tmp_path: Path) -> None:
        observed: list[str] = []

        class Producer:
            @property
            def name(self) -> str:
                return "extract"

            def run(self, context: Any) -> StageOutput:
                context.data["k"] = "v"
                return StageOutput(output={})

        class Consumer:
            @property
            def name(self) -> str:
                return "resolve"

            def run(self, context: Any) -> StageOutput:
                observed.append(context.data["k"])
                return StageOutput(output={})

        _runner(tmp_path, [Producer(), Consumer()]).run()  # type: ignore[list-item]
        assert observed == ["v"]


class TestStageOutputValidation:
    def test_items_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="items_in"):
            StageOutput(output={}, items_in=-1)
        with pytest.raises(ValueError, match="items_out"):
            StageOutput(output={}, items_out=-1)


# ------------------------------------------------- ingestion stages (real)


@pytest.fixture()
def real_env(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "repo"
    write_fixture(
        root,
        {
            "a.py": "def alpha():\n    pass\n",
            "b.py": "def beta():\n    -> alpha\n",
        },
    )
    store = EventStore(tmp_path / "store.sqlite3")
    pipeline = IngestionPipeline(store, extractor=ToyExtractor(), resolver=ToyResolver())
    checkpoints = CheckpointStore(tmp_path / "flow")
    stages = build_ingestion_stages(
        pipeline,
        store,
        root,
        checkpoints=checkpoints,
        seal_path=tmp_path / "flow" / "seal.jsonl",
    )
    return {
        "root": root,
        "store": store,
        "pipeline": pipeline,
        "checkpoints": checkpoints,
        "stages": stages,
        "seal_path": tmp_path / "flow" / "seal.jsonl",
        "tmp": tmp_path,
    }


def _real_runner(env: dict[str, Any], *, policy: RetryPolicy | None = None) -> StagedPipelineRunner:
    return StagedPipelineRunner(
        env["stages"],
        checkpoints=env["checkpoints"],
        input_payload={"root": str(env["root"]), "mode": "full"},
        retry_policy=policy if policy is not None else RetryPolicy(max_attempts=1, jitter_ratio=0.0),
        sleep=lambda _: None,
    )


class TestIngestionStagesReal:
    def test_stage_order_is_the_five_contract_stages(self, real_env: dict[str, Any]) -> None:
        assert [s.name for s in real_env["stages"]] == list(STAGE_ORDER)
        assert [str(n) for n in STAGE_ORDER] == ["extract", "resolve", "synthesize", "index", "seal"]

    def test_full_run_real_store(self, real_env: dict[str, Any]) -> None:
        report = _real_runner(real_env).run()
        assert report.status == "ok"
        records = {r.stage: r for r in report.stages}
        assert records["extract"].items_out >= 2  # two files proposed
        assert records["resolve"].status == "ok"
        assert records["synthesize"].status == "ok"
        assert records["index"].status == "ok"
        assert records["seal"].items_out == 1
        # Resolve really ran the resolver: the -> alpha ref is resolved.
        assert report.to_dict()["stages"][1]["output_digest"]

    def test_extract_checkpoint_records_proposals(self, real_env: dict[str, Any]) -> None:
        _real_runner(real_env).run()
        stages = real_env["checkpoints"].load_stages()
        assert stages["extract"]["status"] == "ok"
        assert len(stages["extract"]["output_digest"]) == 64

    def test_seal_chain_links_runs(self, real_env: dict[str, Any]) -> None:
        _real_runner(real_env).run()
        _real_runner(real_env).run(resume=False)
        assert verify_seal_chain(real_env["seal_path"]) is True
        lines = real_env["seal_path"].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert second["prev_seal_sha256"] == first["seal_sha256"]

    def test_seal_chain_tamper_detected(self, real_env: dict[str, Any]) -> None:
        _real_runner(real_env).run()
        _real_runner(real_env).run(resume=False)
        lines = real_env["seal_path"].read_text(encoding="utf-8").strip().splitlines()
        first = json.loads(lines[0])
        first["input_digest"] = "0" * 64  # tamper
        lines[0] = json.dumps(first)
        real_env["seal_path"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert verify_seal_chain(real_env["seal_path"]) is False

    def test_seal_missing_file_is_vacuously_valid(self, tmp_path: Path) -> None:
        assert verify_seal_chain(tmp_path / "none.jsonl") is True

    def test_resume_drill_real_pipeline(self, real_env: dict[str, Any]) -> None:
        """Kill the run at the index stage; resume must skip extract+resolve+synthesize."""
        original_run = real_env["stages"][3].run  # IndexStage
        calls = {"index": 0}

        def failing_run(context: Any) -> StageOutput:
            calls["index"] += 1
            if calls["index"] == 1:
                raise OSError("disk hiccup at index")
            return original_run(context)

        real_env["stages"][3].run = failing_run  # type: ignore[method-assign]
        runner = _real_runner(real_env)
        with pytest.raises(StageFailedError) as info:
            runner.run()
        assert info.value.stage == "index"
        extract_calls = _count_extracts(real_env)
        report = runner.run(resume=True)
        assert report.status == "ok"
        assert calls["index"] == 2
        assert _count_extracts(real_env) == extract_calls  # extract NOT re-run
        statuses = {r.stage: r.status for r in report.stages}
        assert statuses["extract"] == "resumed"
        assert statuses["resolve"] == "resumed"
        assert statuses["synthesize"] == "resumed"
        assert statuses["index"] == "ok"
        assert statuses["seal"] == "ok"

    def test_index_stage_reports_rebuild(self, real_env: dict[str, Any]) -> None:
        report = _real_runner(real_env).run()
        records = {r.stage: r for r in report.stages}
        assert records["index"].items_out >= 1  # first build indexes rows


def _count_extracts(env: dict[str, Any]) -> int:
    stage = env["stages"][0]
    return getattr(stage, "extract_calls", 0)
