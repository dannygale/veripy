"""FPGA synthesis driver: Yosys + nextpnr for iCE40 and ECP5."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from .fpga import Board, ConstraintSet


# ── Internal helpers ───────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str) -> None:
    """Run a command, printing it, and exit on failure."""
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        sys.exit(f"error: command failed: {' '.join(cmd)}")


def _constraint_filename(cs: ConstraintSet, stem: str) -> str:
    return stem + cs.constraint_ext()


# ── iCE40 flow ─────────────────────────────────────────────────────────────────

def _synth_ice40(
    verilog_path: str,
    top: str,
    board: Board,
    cs: ConstraintSet,
    out_dir: str,
    stem: str,
) -> str:
    """Run Yosys + nextpnr-ice40 + icepack. Returns path to .bin bitstream."""
    json_path = os.path.join(out_dir, f"{stem}.json")
    asc_path  = os.path.join(out_dir, f"{stem}.asc")
    bin_path  = os.path.join(out_dir, f"{stem}.bin")
    pcf_path  = os.path.join(out_dir, f"{stem}.pcf")

    with open(pcf_path, "w") as f:
        f.write(cs.emit_pcf())

    _run(
        ["yosys", "-p",
         f"synth_ice40 -top {top} -json {json_path}",
         verilog_path],
        cwd=out_dir,
    )

    device_flag = f"--{board.part}"
    pkg_flag = ["--package", board.package] if board.package else []
    _run(
        ["nextpnr-ice40", device_flag, *pkg_flag,
         "--json", json_path,
         "--pcf", pcf_path,
         "--asc", asc_path],
        cwd=out_dir,
    )

    _run(["icepack", asc_path, bin_path], cwd=out_dir)
    return bin_path


# ── ECP5 flow ──────────────────────────────────────────────────────────────────

def _synth_ecp5(
    verilog_path: str,
    top: str,
    board: Board,
    cs: ConstraintSet,
    out_dir: str,
    stem: str,
) -> str:
    """Run Yosys + nextpnr-ecp5 + ecppack. Returns path to .bit bitstream."""
    json_path   = os.path.join(out_dir, f"{stem}.json")
    config_path = os.path.join(out_dir, f"{stem}.config")
    bit_path    = os.path.join(out_dir, f"{stem}.bit")
    lpf_path    = os.path.join(out_dir, f"{stem}.lpf")

    with open(lpf_path, "w") as f:
        f.write(cs.emit_lpf())

    _run(
        ["yosys", "-p",
         f"synth_ecp5 -top {top} -json {json_path}",
         verilog_path],
        cwd=out_dir,
    )

    _run(
        ["nextpnr-ecp5",
         "--{part}".format(part=board.part.lower()),
         "--package", board.package,
         "--json", json_path,
         "--lpf", lpf_path,
         "--textcfg", config_path],
        cwd=out_dir,
    )

    _run(["ecppack", config_path, bit_path], cwd=out_dir)
    return bit_path


# ── Public API ─────────────────────────────────────────────────────────────────

def synthesize(
    verilog_src: str,
    board: Board,
    cs: ConstraintSet,
    top: str,
    output_dir: str,
) -> str:
    """Synthesize *verilog_src* for *board* and return the bitstream path.

    Writes intermediate files (JSON, constraint file, bitstream) to
    *output_dir*.  Requires ``yosys``, ``nextpnr-<family>``, and the
    appropriate pack tool on PATH.

    Supported families: ``ice40``, ``ecp5``.
    """
    os.makedirs(output_dir, exist_ok=True)
    stem = top

    v_path = os.path.join(output_dir, f"{stem}.v")
    with open(v_path, "w") as f:
        f.write(verilog_src)

    family = board.family
    if family == "ice40":
        return _synth_ice40(v_path, top, board, cs, output_dir, stem)
    elif family == "ecp5":
        return _synth_ecp5(v_path, top, board, cs, output_dir, stem)
    else:
        sys.exit(
            f"error: synthesis not supported for family '{family}'. "
            "Supported: ice40, ecp5"
        )
