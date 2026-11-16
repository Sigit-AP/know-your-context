"""B3 (SUB-173): the walk cap must never truncate a corpus silently.

Measured baseline (stress-3-products §6 B3): a 1,100-file directory ingested
with the default ``--max-files 1024`` exited 0, wrote 1,024 files, and the only
trace was ``guards_hit max_files`` buried at the end of a long one-line
summary — a 5,000-file repository would silently get 20 % of its index. These
tests pin the fix: the report carries an explicit ``truncated`` /
``files_truncated`` pair, the human output and stderr say ``TRUNCATED`` out
loud, and the CLI default no longer halves a real corpus.
"""

from __future__ import annotations

import json

import pytest

from _ingest_fakes import ToyExtractor, ToyResolver, write_fixture
from know_your_context.cli import build_parser, main
from know_your_context.ingest.pipeline import IngestionBudget


@pytest.fixture(autouse=True)
def fake_sibling_modules(monkeypatch):
    import know_your_context.ingest.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_default_extractor", lambda: ToyExtractor())
    monkeypatch.setattr(pipeline_mod, "_default_resolver", lambda: ToyResolver())


def _corpus(tmp_path, count: int) -> dict[str, str]:
    return {f"m{index:02d}.py": f"def fn_{index}():\n    pass\n" for index in range(count)}


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_default_max_files_does_not_truncate_a_real_repo() -> None:
    """The 1,024 default silently cut 5k-file repos; the CLI default is the max."""
    args = build_parser().parse_args(["ingest", "some/path"])
    assert args.max_files == 65536


def test_json_report_flags_truncation(tmp_path, capsys) -> None:
    write_fixture(tmp_path, _corpus(tmp_path, 12))
    code, out, err = _run(
        capsys, "--store", str(tmp_path / "s.sqlite3"), "ingest", "--json", "--max-files", "5", str(tmp_path)
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["truncated"] is True
    assert payload["files_truncated"] == 7
    assert payload["stats"]["scanned"] == 5
    assert payload["max_files"] == 5
    assert "TRUNCATED" in err, "a silent cap is the bug: the operator must be told"


def test_untruncated_report_is_explicitly_false(tmp_path, capsys) -> None:
    write_fixture(tmp_path, _corpus(tmp_path, 4))
    code, out, err = _run(
        capsys, "--store", str(tmp_path / "s.sqlite3"), "ingest", "--json", "--max-files", "5", str(tmp_path)
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["truncated"] is False
    assert payload["files_truncated"] == 0
    assert "TRUNCATED" not in err


def test_human_summary_leads_with_the_truncation(tmp_path, capsys) -> None:
    write_fixture(tmp_path, _corpus(tmp_path, 8))
    code, out, err = _run(capsys, "--store", str(tmp_path / "s.sqlite3"), "ingest", "--max-files", "3", str(tmp_path))
    assert code == 0, err
    assert out.startswith("ingest: TRUNCATED"), out
    assert "max_files=3" in out
    assert "5 file(s) not ingested" in out


def test_budget_default_is_the_documented_maximum() -> None:
    """A bare ``IngestionBudget()`` must not encode a silent 1,024-file cap."""
    assert IngestionBudget().max_files == 65536
