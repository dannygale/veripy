"""FPGA synthesis driver: Yosys + nextpnr for iCE40 and ECP5."""

from __future__ import annotations

import os
import subprocess
import sys

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


# ── Vivado flow ────────────────────────────────────────────────────────────────

def _synth_vivado(
    verilog_path: str,
    top: str,
    board: Board,
    cs: ConstraintSet,
    out_dir: str,
    stem: str,
) -> str:
    """Run Vivado in batch mode. Returns path to .bit bitstream."""
    xdc_path = os.path.join(out_dir, f"{stem}.xdc")
    bit_path  = os.path.join(out_dir, f"{stem}.bit")
    tcl_path  = os.path.join(out_dir, f"{stem}_vivado.tcl")

    with open(xdc_path, "w") as f:
        f.write(cs.emit_xdc())

    tcl = (
        f"read_verilog {verilog_path}\n"
        f"read_xdc {xdc_path}\n"
        f"synth_design -top {top} -part {board.part}\n"
        f"opt_design\n"
        f"place_design\n"
        f"route_design\n"
        f"write_bitstream -force {bit_path}\n"
    )
    with open(tcl_path, "w") as f:
        f.write(tcl)

    _run(["vivado", "-mode", "batch", "-source", tcl_path], cwd=out_dir)
    return bit_path


# ── Quartus flow ───────────────────────────────────────────────────────────────

def _synth_quartus(
    verilog_path: str,
    top: str,
    board: Board,
    cs: ConstraintSet,
    out_dir: str,
    stem: str,
) -> str:
    """Run Quartus in batch mode. Returns path to .sof bitstream."""
    sdc_path = os.path.join(out_dir, "constraints.sdc")
    qsf_path = os.path.join(out_dir, f"{stem}.qsf")
    qpf_path = os.path.join(out_dir, f"{stem}.qpf")
    sof_path = os.path.join(out_dir, "output_files", f"{stem}.sof")

    with open(sdc_path, "w") as f:
        f.write(cs.emit_sdc())

    qsf = (
        f"set_global_assignment -name DEVICE {board.part}\n"
        f"set_global_assignment -name TOP_LEVEL_ENTITY {top}\n"
        f"set_global_assignment -name VERILOG_FILE {verilog_path}\n"
        f"set_global_assignment -name SDC_FILE {sdc_path}\n"
    )
    for port, loc, io_std in cs._pins:
        qsf += f"set_location_assignment PIN_{loc} -to {port}\n"
        std = io_std or "3.3-V LVTTL"
        qsf += f"set_instance_assignment -name IO_STANDARD \"{std}\" -to {port}\n"
    with open(qsf_path, "w") as f:
        f.write(qsf)

    with open(qpf_path, "w") as f:
        f.write(f"PROJECT_REVISION = \"{stem}\"\n")

    _run(["quartus_sh", "--flow", "compile", stem], cwd=out_dir)
    return sof_path


# ── Programming ───────────────────────────────────────────────────────────────

def program(bitstream_path: str, board: Board, programmer: str | None = None) -> None:
    """Program *bitstream_path* onto *board*.

    Selects the programmer automatically based on board family unless
    *programmer* is given explicitly (``"iceprog"`` or ``"openFPGALoader"``).

    - ``ice40``: ``iceprog`` by default (``openFPGALoader`` also accepted)
    - ``ecp5``, ``xilinx``, ``intel``, ``gowin``: ``openFPGALoader``
    """
    family = board.family
    if programmer is None:
        programmer = "iceprog" if family == "ice40" else "openFPGALoader"

    if programmer == "iceprog":
        cmd = ["iceprog", bitstream_path]
    elif programmer == "openFPGALoader":
        cmd = ["openFPGALoader", "-b", board.name, bitstream_path]
    else:
        sys.exit(f"error: unknown programmer '{programmer}'. Use 'iceprog' or 'openFPGALoader'")

    _run(cmd, cwd=os.path.dirname(os.path.abspath(bitstream_path)))


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
    *output_dir*.  Requires the appropriate vendor tools on PATH:
    ``yosys``/``nextpnr`` for open-source targets, ``vivado`` for Xilinx,
    ``quartus_sh`` for Intel.

    Supported families: ``ice40``, ``ecp5``, ``xilinx``, ``intel``.
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
    elif family == "xilinx":
        return _synth_vivado(v_path, top, board, cs, output_dir, stem)
    elif family == "intel":
        return _synth_quartus(v_path, top, board, cs, output_dir, stem)
    else:
        sys.exit(
            f"error: synthesis not supported for family '{family}'. "
            "Supported: ice40, ecp5, xilinx, intel"
        )
