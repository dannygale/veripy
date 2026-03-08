"""Parallel test runner: discover test files and run them concurrently."""

import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


def _discover_test_files(path):
    """Return list of test_*.py file paths under *path*."""
    files = []
    for root, _dirs, names in os.walk(path):
        for name in sorted(names):
            if name.startswith("test_") and name.endswith(".py"):
                files.append(os.path.join(root, name))
    return files


def _run_file(args):
    """Run a single test file; return (path, returncode, output)."""
    path, verbose = args
    cmd = [sys.executable, "-m", "unittest", path.replace(os.sep, ".").removesuffix(".py")]
    # Convert file path to dotted module name relative to cwd
    rel = os.path.relpath(path)
    module = rel.replace(os.sep, ".").removesuffix(".py")
    cmd = [sys.executable, "-m", "unittest", module]
    if verbose:
        cmd.append("-v")
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr + result.stdout  # unittest writes to stderr
    return path, result.returncode, output


def run_parallel(path, jobs=None, verbose=False):
    """Discover and run test files under *path* in parallel.

    Args:
        path: Directory to discover tests in.
        jobs: Number of parallel workers (default: cpu_count).
        verbose: Pass -v to each test run.

    Returns:
        0 if all tests pass, 1 otherwise.
    """
    files = _discover_test_files(path)
    if not files:
        print("No test files found.", file=sys.stderr)
        return 1

    workers = jobs or os.cpu_count() or 4
    workers = min(workers, len(files))

    passed = []
    failed = []
    t0 = time.monotonic()

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_file, (f, verbose)): f for f in files}
        for fut in as_completed(futures):
            fpath, rc, output = fut.result()
            name = os.path.basename(fpath)
            # Extract summary line (last non-empty line of output)
            lines = [l for l in output.splitlines() if l.strip()]
            summary = lines[-1] if lines else ""
            if rc == 0:
                passed.append(name)
                status = "ok"
            else:
                failed.append((name, output))
                status = "FAIL"
            print(f"  [{status:4s}] {name}  {summary}")

    elapsed = time.monotonic() - t0
    total = len(passed) + len(failed)
    print(f"\n{total} files | {len(passed)} passed | {len(failed)} failed | {elapsed:.1f}s")

    if failed:
        print("\n--- Failures ---")
        for name, output in failed:
            print(f"\n=== {name} ===")
            print(output)
        return 1
    return 0
