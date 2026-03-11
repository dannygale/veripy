"""Parallel test runner: discover test files and run them concurrently.

Granularity
-----------
Work is split by *file*, not by individual test class or method.  All
test cases inside a single file run sequentially in the same worker
process.

Why not per-method?  Three reasons:

1. **Build artifact races** — cysim compilation writes to
   ``build/csim/cysim/``.  Two workers compiling the same module
   simultaneously would race on the ``.so`` file.  The content-hash
   cache avoids redundant compiles but doesn't lock the first build.

2. **Process overhead** — spawning a process per test method means each
   worker re-imports the module, re-loads the ``.so``, and
   re-initializes.  For tests that finish in < 50 ms the startup cost
   dominates.

3. **Shared state** — ``_cysim_cache`` is a process-local dict; each
   worker would cold-start its own cache.

A middle ground (not yet implemented) would be to split by *test class*
rather than file.  This would help files that contain multiple
``TestBench`` subclasses while keeping the per-class setup cost
amortised across its methods.  The main prerequisite is per-worker temp
dirs or a file lock around the cysim compile step.
"""

import json
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

_SEEDS_FILE = ".veripy_seeds.json"
_BASELINE_FILE = ".veripy_baseline.json"


def _discover_test_files(path):
    """Return list of test_*.py file paths under *path*."""
    files = []
    for root, _dirs, names in os.walk(path):
        for name in sorted(names):
            if name.startswith("test_") and name.endswith(".py"):
                files.append(os.path.join(root, name))
    return files


def _run_file(args):
    """Run a single test file; return (path, returncode, output, seed)."""
    path, verbose, coverage_file, seed = args
    rel = os.path.relpath(path)
    module = rel.replace(os.sep, ".").removesuffix(".py")
    cmd = [sys.executable, "-m", "unittest", module]
    if verbose:
        cmd.append("-v")
    env = os.environ.copy()
    if coverage_file:
        env['VERIPY_COVERAGE_FILE'] = coverage_file
    env['VERIPY_SEED'] = str(seed)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    output = result.stderr + result.stdout
    return path, result.returncode, output, seed


def _merge_coverage(coverage_files):
    """Read and merge coverage JSON files; return {name: hit_count}."""
    merged = {}
    for path in coverage_files:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            for name, hits in data.items():
                merged[name] = merged.get(name, 0) + hits
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def _print_coverage(merged):
    """Print a coverage summary table."""
    if not merged:
        return
    print("\n--- Coverage Report ---")
    total = len(merged)
    hit = sum(1 for v in merged.values() if v > 0)
    for name, hits in sorted(merged.items()):
        status = "hit" if hits > 0 else "MISS"
        print(f"  [{status:4s}] {name}  ({hits} hits)")
    print(f"{hit}/{total} cover points hit")


def _load_seeds(seed_file):
    """Load seed map {path: seed} from file."""
    if os.path.exists(seed_file):
        try:
            with open(seed_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_seeds(seed_map, seed_file):
    """Persist seed map to file."""
    with open(seed_file, "w") as f:
        json.dump(seed_map, f, indent=2)


def _load_baseline(baseline_file):
    """Load baseline {path: 'pass'|'fail'} from file."""
    if os.path.exists(baseline_file):
        try:
            with open(baseline_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_baseline(results, baseline_file):
    """Save {path: 'pass'|'fail'} baseline."""
    with open(baseline_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Baseline saved to {baseline_file} ({len(results)} entries)")


def run_parallel(path, jobs=None, verbose=False, seed=None,
                 save_baseline=False, regression=False):
    """Discover and run test files under *path* in parallel.

    Args:
        path: Directory to discover tests in.
        jobs: Number of parallel workers (default: cpu_count).
        verbose: Pass -v to each test run.
        seed: Fixed seed for all tests (int). If None, random seeds are used.
        save_baseline: Write pass/fail results to .veripy_baseline.json.
        regression: Compare results against .veripy_baseline.json and report regressions.

    Returns:
        0 if all tests pass (and no regressions in regression mode), 1 otherwise.
    """
    files = _discover_test_files(path)
    if not files:
        print("No test files found.", file=sys.stderr)
        return 1

    workers = jobs or os.cpu_count() or 4
    workers = min(workers, len(files))

    # Assign seeds: fixed seed fans out per-file, else random per file
    if seed is not None:
        rng = random.Random(seed)
        seeds = {f: rng.randint(0, 2**31 - 1) for f in files}
    else:
        # Reuse recorded seeds for reproducibility; generate new ones otherwise
        recorded = _load_seeds(_SEEDS_FILE)
        seeds = {f: recorded.get(f, random.randint(0, 2**31 - 1)) for f in files}

    passed = []
    failed = []
    results_map = {}  # path → 'pass'|'fail'
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as cov_dir:
        cov_files = {f: os.path.join(cov_dir, f"{i}.json") for i, f in enumerate(files)}

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_run_file, (f, verbose, cov_files[f], seeds[f])): f
                for f in files
            }
            for fut in as_completed(futures):
                fpath, rc, output, used_seed = fut.result()
                name = os.path.basename(fpath)
                lines = [l for l in output.splitlines() if l.strip()]
                summary = lines[-1] if lines else ""
                if rc == 0:
                    passed.append(fpath)
                    results_map[fpath] = "pass"
                    status = "ok"
                    print(f"  [{status:4s}] {name}  {summary}")
                else:
                    failed.append((fpath, output, used_seed))
                    results_map[fpath] = "fail"
                    status = "FAIL"
                    print(f"  [{status:4s}] {name}  {summary}  (seed={used_seed})")

        merged = _merge_coverage(list(cov_files.values()))

    elapsed = time.monotonic() - t0
    total = len(passed) + len(failed)
    print(f"\n{total} files | {len(passed)} passed | {len(failed)} failed | {elapsed:.1f}s")

    _print_coverage(merged)

    # Save seeds for all runs so failures can be replayed
    _save_seeds({f: seeds[f] for f in files}, _SEEDS_FILE)

    if failed:
        print("\n--- Failures ---")
        for fpath, output, used_seed in failed:
            name = os.path.basename(fpath)
            print(f"\n=== {name} (seed={used_seed}) ===")
            print(output)
            print(f"  Replay: veripy test {fpath} --seed {used_seed}")

    if save_baseline:
        _save_baseline(results_map, _BASELINE_FILE)

    if regression:
        baseline = _load_baseline(_BASELINE_FILE)
        if not baseline:
            print("No baseline found. Run with --save-baseline first.", file=sys.stderr)
            return 1
        regressions = [f for f, r in results_map.items() if r == "fail" and baseline.get(f) == "pass"]
        fixed = [f for f, r in results_map.items() if r == "pass" and baseline.get(f) == "fail"]
        if regressions:
            print("\n--- Regressions (newly failing) ---")
            for f in regressions:
                print(f"  REGRESSED  {os.path.basename(f)}")
        if fixed:
            print("\n--- Fixed (previously failing) ---")
            for f in fixed:
                print(f"  FIXED      {os.path.basename(f)}")
        if not regressions and not fixed:
            print("\nNo regressions.")
        if regressions:
            return 1

    return 1 if failed else 0
