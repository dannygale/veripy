# VeriPy Work Log

## 2026-03-09 — multi-model-testing (partial)

### What was done

**veripy/cli.py**
- Fixed missing `def cmd_test(args):` — the function body was floating unreachable code after `cmd_check` ended. The `def` line had been accidentally dropped.
- Added `--model` argument to `veripy test` (`choices=['functional', 'cycle', 'rtl']`).
- `cmd_test` now sets `VERIPY_MODEL` env var when `--model` is passed, which `_resolve_models()` reads.

**veripy/verify.py**
- Added `_MODEL_ORDER = ['functional', 'cycle', 'rtl']`.
- Added `_resolve_models(cls, mod)`: auto-detects available models from the module instance (`_functional`, `_cycle`, `_always_blocks`/`_comb_blocks`). Respects `VERIPY_MODEL` env var and `model` class attribute as a minimum fidelity floor.
- Added `model = None` class attribute to `TestBench`.
- Rewrote `_wrap_testbench()` to iterate over resolved models: functional/cycle passes run the Python sim with the appropriate model active; the rtl pass runs the existing behavioral + compiled backend path. Cross-check via `_assert_all_match()` compares all collected outputs.

**pm**
- Resolved #202, #203, #205. Closed #204 as wontfix (by design: functional/cycle have no IR, csim only applies to rtl pass). Feature `multi-model-testing` marked partial — #206 (docs + migration) remains open.

### Issues found
- None.

### What is next
- #206: Update docs/testing.md and migrate existing TestBench usages to use `model` attribute where appropriate.
