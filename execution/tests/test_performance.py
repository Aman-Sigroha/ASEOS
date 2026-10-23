"""Performance guard rails: the benchmark functions at small sizes with
generous budgets. They exist to catch an order-of-magnitude regression (an
accidental O(n^2), a read of a whole log into memory, serialization of things
that should run side by side), not to measure. Real numbers: `python -m
execution.bench`, recorded in docs/EXECUTION_LIMITATIONS.md.

Budgets are several times what a slow Windows laptop with real-time antivirus
needed when they were written, because the first read of a newly written file
there costs about ten milliseconds."""

import asyncio

import pytest

from execution import bench
from execution.tests import qualification_repos as repos


@pytest.fixture
def work(tmp_path):
    return tmp_path


def test_a_command_costs_less_than_a_second_more_than_running_it_directly(work):
    repo = repos.python_project(work / "repo")
    result = asyncio.run(bench.bench_commands(repo, work / "w", 4))

    assert result["overhead_p50_ms"] < 1000, result


def test_reading_patching_and_writing_are_fast(work):
    repo = repos.python_project(work / "repo")
    result = asyncio.run(bench.bench_reads_and_patches(repo, work / "w", 6))

    assert result["read_10kb"]["p50_ms"] < 200, result
    assert result["read_880kb"]["p50_ms"] < 500, result
    assert result["patch_5000_line_file"]["p50_ms"] < 2000, result
    assert result["write_10kb"]["p50_ms"] < 1000, result
    assert result["search_tree"]["p50_ms"] < 2000, result


def test_git_status_and_diff_on_a_few_hundred_files(work):
    repo = repos.big_repo(work / "repo", files=300)
    result = asyncio.run(bench.bench_git(repo, work / "w", 3, 300))

    assert result["status_clean"]["p50_ms"] < 3000, result
    assert result["status_50_modified"]["p50_ms"] < 3000, result
    assert result["diff_50_modified"]["p50_ms"] < 8000, result
    assert result["workspace_version"]["p50_ms"] < 8000, result


def test_creating_and_removing_a_worktree(work):
    repo = repos.big_repo(work / "repo", files=300)
    result = asyncio.run(bench.bench_worktrees(repo, work / "w", 2, 300))

    assert result["create"]["p50_ms"] < 20_000, result
    assert result["remove"]["p50_ms"] < 20_000, result


def test_a_test_run_costs_little_more_than_pytest_itself(work):
    repo = repos.python_project(work / "repo")
    result = asyncio.run(bench.bench_tests(repo, work / "w", 3))

    assert result["overhead_p50_ms"] < 4000, result


def test_forty_megabytes_of_output_does_not_grow_memory_or_the_result(work):
    repo = repos.python_project(work / "repo")
    result = asyncio.run(bench.bench_memory_under_output_flood(repo, work / "w", 40))

    assert result["success"], result
    assert result["result_truncated"], result
    assert result["result_stdout_chars"] < 1_000_000, result
    assert result["memory_growth_mb"] < 250, result  # 40 MB were printed


def test_parallel_work_is_parallel_and_writes_still_complete(work):
    repo = repos.python_project(work / "repo")
    result = asyncio.run(bench.bench_concurrency(repo, work / "w", 8))

    assert result["parallel_reads"]["ok"] and result["parallel_reads"]["total_ms"] < 10_000, result
    assert result["parallel_commands_0_5s_each"]["ok"], result
    # run one after another these eight would need over 4 s of sleeping alone
    assert result["parallel_commands_0_5s_each"]["total_s"] < 3.5, result
    assert result["serialized_writes"]["ok"], result


def test_the_artifact_store_stays_usable_with_a_few_hundred_artifacts(work):
    result = bench.bench_artifacts(work / "store", 200)

    assert result["found"] == 200, result
    assert result["audit_clean"] is True, result
    assert result["find_all_warm_ms"] < 2000, result
    assert result["gc_ms"] < 5000, result
    assert result["disk_mb"] < 5, result


def test_storing_an_artifact_does_not_add_up_every_stored_blob_each_time(work, monkeypatch):
    """Counting all blobs per put made 3000 artifacts cost ~50 ms each (O(n^2))."""

    from execution.artifacts import ArtifactStore, Owner

    store = ArtifactStore(work / "store")
    scans = []
    real = store.total_bytes
    monkeypatch.setattr(store, "total_bytes", lambda: scans.append(1) or real())
    owner = Owner("ws", "task", "action", "tool")

    for _ in range(60):
        store.put("log line\n" * 20, kind="command_log", owner=owner)

    assert len(scans) <= 2, f"the store counted its blobs {len(scans)} times for 60 puts"


def test_the_quota_is_still_enforced_when_the_estimate_is_not_trusted(work):
    from execution.artifacts import ArtifactError, ArtifactStore, Owner, RetentionPolicy

    store = ArtifactStore(work / "store", RetentionPolicy(max_total_bytes=5000))
    owner = Owner("ws", "task", "action", "tool")
    stored = 0

    with pytest.raises(ArtifactError) as raised:
        for _ in range(100):
            store.put("x" * 400, kind="audit_record", owner=owner, retention="audit")
            stored += 1

    assert raised.value.kind == "full"
    assert 5 <= stored <= 12  # about 5000 / 400, not 100
