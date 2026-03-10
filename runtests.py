#!/usr/bin/env python3
"""Run VeriPy library test suites.

Usage:
    python runtests.py                  # list suites
    python runtests.py fast             # quick smoke tests (~5s)
    python runtests.py core sim         # multiple suites
    python runtests.py all              # everything
    python runtests.py all -x csim      # everything except csim
    python runtests.py -k "pipeline"    # unittest -k passthrough
    python runtests.py fast -v          # verbose
"""

import sys
import unittest

SUITES = {
    "core": [
        "test_signal", "test_flatten", "test_dce", "test_emitter",
        "test_lint", "test_mem_inference", "test_import",
    ],
    "sim": [
        "test_sim_engine", "test_reactive", "test_driver", "test_rand",
        "test_behavioral", "test_multi_model", "test_dual_path",
    ],
    "modules": [
        "test_counter", "test_module_decorator", "test_fsm",
        "test_pipeline", "test_pipe_stage", "test_named_stage",
        "test_pipeline_enhanced", "test_interface", "test_blackbox",
        "test_port_arrays", "test_standalone_features",
    ],
    "csim": [
        "test_csim_backend", "test_csim_temporal", "test_csim_coverage",
        "test_csim_model_tb_split", "test_hier_csim",
    ],
    "cpu": [
        "test_alu_v1", "test_decode_v1", "test_hazard_unit",
        "test_datapath", "test_regfile", "test_firmware_cosim",
        "test_gdb_stub",
    ],
    "ip": [
        "test_ip", "test_csr", "test_axi4", "test_axi4lite",
        "test_cdc", "test_spi_controller", "test_soc",
        "test_dual_port_mem",
    ],
    "formal": [
        "test_formal", "test_formal_backend", "test_equiv_backend",
        "test_sva",
    ],
    "backends": [
        "test_verilator", "test_wgpu_backend", "test_fpga",
    ],
    "infra": [
        "test_config", "test_project", "test_packaging", "test_autodoc",
    ],
}

# Meta suites
_ALL = [m for modules in SUITES.values() for m in modules]
SUITES["all"] = _ALL
SUITES["fast"] = [m for m in _ALL if m not in (
    # >10s each — skip for fast iteration
    "test_csim_backend", "test_csim_temporal", "test_csim_coverage",
    "test_csim_model_tb_split", "test_verilator",
    "test_alu_v1", "test_decode_v1", "test_hazard_unit", "test_ip",
)]


def main():
    args = sys.argv[1:]
    verbose = "-v" in args
    if verbose:
        args.remove("-v")

    # -k filter passthrough
    k_filter = None
    if "-k" in args:
        idx = args.index("-k")
        k_filter = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    # -x exclude suites
    exclude = set()
    while "-x" in args:
        idx = args.index("-x")
        ex = args[idx + 1]
        if ex in SUITES:
            exclude.update(SUITES[ex])
        else:
            exclude.add(ex)
        args = args[:idx] + args[idx + 2:]

    suite_names = args or []

    if not suite_names:
        print("Available suites:\n")
        for name, modules in SUITES.items():
            if name in ("all", "fast"):
                continue
            print(f"  {name:10s}  {len(modules)} files  ({', '.join(modules[:3])}{'...' if len(modules) > 3 else ''})")
        print(f"\n  {'fast':10s}  {len(SUITES['fast'])} files  (all minus slow compilation tests)")
        print(f"  {'all':10s}  {len(SUITES['all'])} files  (everything)")
        print(f"\nUsage: python runtests.py <suite> [<suite>...] [-x <exclude>] [-k <pattern>] [-v]")
        return

    # Collect modules
    modules = []
    for name in suite_names:
        if name in SUITES:
            modules.extend(SUITES[name])
        else:
            modules.append(name)
    # Dedupe preserving order, apply excludes
    seen = set()
    final = []
    for m in modules:
        if m not in seen and m not in exclude:
            seen.add(m)
            final.append(m)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for m in final:
        suite.addTests(loader.loadTestsFromName(f"tests.{m}"))

    if k_filter:
        # Filter tests by -k pattern
        filtered = unittest.TestSuite()
        for group in suite:
            for test in group:
                if k_filter in str(test):
                    filtered.addTest(test)
        suite = filtered

    verbosity = 2 if verbose else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
