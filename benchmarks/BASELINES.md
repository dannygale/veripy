# C-Sim Benchmark Baselines

## 2026-03-05 — after per-edge-group NBA optimization

### SPI Controller (single module)

| Xfers | ~Cycles | C-native exec | Vltr exec | Vltr/C |
|-------|---------|---------------|-----------|--------|
| 100 | 3.8K | 0.0000s | 0.0005s | C wins |
| 1K | 38K | 0.0004s | 0.0013s | 2.9× |
| 10K | 380K | 0.0062s | 0.0102s | 1.6× |

Compile: C 0.16s vs Vltr 4.06s (25×)

### SpiHub (4× SPI + arbiter, 129 signals, 56 comb, 13 seq, 4 mem)

| Xfers | C-native exec | Vltr exec | Vltr/C | Total C | Total Vltr |
|-------|---------------|-----------|--------|---------|------------|
| 1K | 0.003s | 0.002s | 0.63× | 0.17s | 3.69s |
| 10K | 0.018s | 0.014s | 0.77× | 0.25s | 3.91s |
| 100K | 0.158s | 0.133s | 0.84× | 0.37s | 4.10s |

Compile: C 0.22s vs Vltr 3.9s (18×)

Vltr/C < 1.0 = Verilator faster on execution. C always wins total turnaround (11-21×).
