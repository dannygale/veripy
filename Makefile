.PHONY: test test-frontend test-middle test-backend-csim test-backend-verilog \
       test-backend-verilator test-backend-formal test-cross test-lib clean

# ── All tests, parallel ──────────────────────────────────────────────
test:
	pytest tests/ -n auto --tb=short -q

# ── Frontend: signals, decorator, AST rewriting, module features ─────
FRONTEND := tests/test_signal.py tests/test_module_decorator.py \
            tests/test_standalone_features.py tests/test_port_arrays.py \
            tests/test_behavioral.py tests/test_blackbox.py \
            tests/test_interface.py tests/test_fsm.py \
            tests/test_pipe_stage.py tests/test_named_stage.py \
            tests/test_pipeline.py tests/test_pipeline_enhanced.py \
            tests/test_dual_port_mem.py tests/test_mem_inference.py \
            tests/test_formal.py tests/test_cdc.py \
            tests/test_sim_engine.py tests/test_reactive.py \
            tests/test_counter.py tests/test_rand.py tests/test_driver.py

test-frontend:
	pytest $(FRONTEND) -n auto --tb=short -q

# ── Middle: IR, lowering, flatten, DCE, lint, emission ───────────────
MIDDLE := tests/test_flatten.py tests/test_datapath.py tests/test_dce.py \
          tests/test_emitter.py tests/test_lint.py tests/test_import.py

test-middle:
	pytest $(MIDDLE) -n auto --tb=short -q

# ── Backend: csim ────────────────────────────────────────────────────
BACKEND_CSIM := tests/test_csim_backend.py tests/test_csim_coverage.py \
                tests/test_csim_temporal.py tests/test_csim_model_tb_split.py \
                tests/test_hier_csim.py

test-backend-csim:
	pytest $(BACKEND_CSIM) -n auto --tb=short -q

# ── Backend: Verilog emission + iverilog ─────────────────────────────
BACKEND_VERILOG := tests/test_emitter.py

test-backend-verilog:
	pytest $(BACKEND_VERILOG) --tb=short -q

# ── Backend: Verilator ───────────────────────────────────────────────
test-backend-verilator:
	pytest tests/test_verilator.py --tb=short -q

# ── Backend: formal (SymbiYosys, equivalence) ────────────────────────
BACKEND_FORMAL := tests/test_formal_backend.py tests/test_equiv_backend.py

test-backend-formal:
	pytest $(BACKEND_FORMAL) --tb=short -q

# ── Cross-backend: VeripyTestCase multi-backend comparison ───────────
CROSS := tests/test_dual_path.py tests/test_alu_v1.py \
         tests/test_decode_v1.py tests/test_hazard_unit.py \
         tests/test_ip.py tests/test_spi_controller.py \
         tests/test_regfile.py

test-cross:
	pytest $(CROSS) -n auto --tb=short -q

# ── Library: IP, SoC, FPGA, CSR, AXI, tooling ───────────────────────
LIB := tests/test_csr.py tests/test_axi4lite.py tests/test_axi4.py \
       tests/test_soc.py tests/test_fpga.py tests/test_firmware_cosim.py \
       tests/test_gdb_stub.py tests/test_sva.py tests/test_config.py \
       tests/test_project.py tests/test_autodoc.py tests/test_packaging.py \
       tests/test_wgpu_backend.py

test-lib:
	pytest $(LIB) -n auto --tb=short -q

# ── Build ────────────────────────────────────────────────────────────
clean:
	rm -rf build/
