"""FPGA board definitions, constraint management, and constraint file emission."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class PinDef:
    """A named pin on a board: physical location and optional IO standard."""
    loc: str
    io_std: Optional[str] = None


@dataclass
class ClockDef:
    """A clock source on a board: pin name and frequency."""
    pin: str          # board pin name (key in Board.pins)
    freq_mhz: float


# ── Board ──────────────────────────────────────────────────────────────────────

class Board:
    """FPGA board definition: part, family, pin map, clock sources.

    *family* is one of: ``ice40``, ``ecp5``, ``gowin``, ``xilinx``, ``intel``.
    *pins* maps logical signal names to :class:`PinDef` objects.
    *clocks* maps clock names to :class:`ClockDef` objects.
    """

    def __init__(
        self,
        name: str,
        family: str,
        part: str,
        package: str = "",
        pins: dict[str, PinDef] | None = None,
        clocks: dict[str, ClockDef] | None = None,
    ):
        self.name = name
        self.family = family
        self.part = part
        self.package = package
        self.pins: dict[str, PinDef] = pins or {}
        self.clocks: dict[str, ClockDef] = clocks or {}

    def constraint_set(self, port_map: dict[str, str]) -> "ConstraintSet":
        """Build a :class:`ConstraintSet` by mapping module ports to board pins.

        *port_map* maps module port names to board pin names (keys in
        ``self.pins``).  Raises ``KeyError`` if a board pin name is not found.
        """
        cs = ConstraintSet(self)
        for port, pin_name in port_map.items():
            pin = self.pins[pin_name]
            cs.pin(port, pin.loc, pin.io_std)
        # Auto-add clock constraints for any port mapped to a clock pin
        pin_to_port = {v: k for k, v in port_map.items()}
        for clk_name, clk in self.clocks.items():
            if clk.pin in pin_to_port:
                cs.clock(pin_to_port[clk.pin], clk.freq_mhz)
        return cs


# ── ConstraintSet ──────────────────────────────────────────────────────────────

class ConstraintSet:
    """Collected pin and timing constraints for a specific module/board pairing."""

    def __init__(self, board: Board):
        self.board = board
        self._pins: list[tuple[str, str, Optional[str]]] = []   # (port, loc, io_std)
        self._clocks: list[tuple[str, float]] = []              # (port, freq_mhz)

    def pin(self, port: str, loc: str, io_std: Optional[str] = None) -> "ConstraintSet":
        self._pins.append((port, loc, io_std))
        return self

    def clock(self, port: str, freq_mhz: float) -> "ConstraintSet":
        self._clocks.append((port, freq_mhz))
        return self

    # ── Emission ───────────────────────────────────────────────────────────────

    def emit_pcf(self) -> str:
        """Emit iCE40 PCF constraint file."""
        lines = []
        for port, loc, _ in self._pins:
            lines.append(f"set_io {port} {loc}")
        for port, freq_mhz in self._clocks:
            period_ns = 1000.0 / freq_mhz
            lines.append(f"set_frequency {port} {freq_mhz}")
        return "\n".join(lines) + ("\n" if lines else "")

    def emit_lpf(self) -> str:
        """Emit ECP5 LPF constraint file."""
        lines = []
        for port, loc, io_std in self._pins:
            std = io_std or "LVCMOS33"
            lines.append(f'LOCATE COMP "{port}" SITE "{loc}";')
            lines.append(f'IOBUF PORT "{port}" IO_TYPE={std};')
        for port, freq_mhz in self._clocks:
            period_ns = 1000.0 / freq_mhz
            lines.append(f'FREQUENCY PORT "{port}" {freq_mhz:.3f} MHZ;')
        return "\n".join(lines) + ("\n" if lines else "")

    def emit_xdc(self) -> str:
        """Emit Xilinx XDC constraint file."""
        lines = []
        for port, loc, io_std in self._pins:
            lines.append(f"set_property PACKAGE_PIN {loc} [get_ports {{{port}}}]")
            std = io_std or "LVCMOS33"
            lines.append(f"set_property IOSTANDARD {std} [get_ports {{{port}}}]")
        for port, freq_mhz in self._clocks:
            period_ns = 1000.0 / freq_mhz
            lines.append(
                f"create_clock -period {period_ns:.3f} -name {port} "
                f"[get_ports {{{port}}}]"
            )
        return "\n".join(lines) + ("\n" if lines else "")

    def emit_qsf(self) -> str:
        """Emit Intel Quartus QSF settings file."""
        lines = [
            f"set_global_assignment -name DEVICE {self.board.part}",
        ]
        for port, loc, io_std in self._pins:
            lines.append(f"set_location_assignment PIN_{loc} -to {port}")
            std = io_std or "3.3-V LVTTL"
            lines.append(f"set_instance_assignment -name IO_STANDARD \"{std}\" -to {port}")
        for port, freq_mhz in self._clocks:
            period_ns = 1000.0 / freq_mhz
            lines.append(
                f"set_global_assignment -name SDC_FILE constraints.sdc"
            )
            break  # SDC file added once; clocks go in SDC
        return "\n".join(lines) + ("\n" if lines else "")

    def emit_sdc(self) -> str:
        """Emit SDC timing constraints (used by Quartus and Vivado)."""
        lines = []
        for port, freq_mhz in self._clocks:
            period_ns = 1000.0 / freq_mhz
            lines.append(
                f"create_clock -period {period_ns:.3f} -name {port} "
                f"[get_ports {{{port}}}]"
            )
        return "\n".join(lines) + ("\n" if lines else "")

    def emit(self) -> str:
        """Emit the appropriate constraint file for the board's family."""
        family = self.board.family
        if family == "ice40":
            return self.emit_pcf()
        elif family == "ecp5":
            return self.emit_lpf()
        elif family in ("xilinx", "gowin"):
            return self.emit_xdc()
        elif family == "intel":
            return self.emit_qsf()
        else:
            raise ValueError(f"No constraint emitter for family '{family}'")

    def constraint_ext(self) -> str:
        """Return the file extension for this board's constraint format."""
        family = self.board.family
        if family == "ice40":
            return ".pcf"
        elif family == "ecp5":
            return ".lpf"
        elif family in ("xilinx", "gowin"):
            return ".xdc"
        elif family == "intel":
            return ".qsf"
        else:
            return ".cst"


# ── Built-in board definitions ─────────────────────────────────────────────────

def _icebreaker() -> Board:
    pins = {
        "CLK":      PinDef("35"),
        "LED_R_N":  PinDef("11"),
        "LED_G_N":  PinDef("37"),
        "BTN_N":    PinDef("10"),
        "UART_TX":  PinDef("6"),
        "UART_RX":  PinDef("9"),
        # PMOD 1A
        "P1A1":  PinDef("4"),
        "P1A2":  PinDef("2"),
        "P1A3":  PinDef("47"),
        "P1A4":  PinDef("45"),
        "P1A7":  PinDef("3"),
        "P1A8":  PinDef("48"),
        "P1A9":  PinDef("46"),
        "P1A10": PinDef("44"),
        # PMOD 1B
        "P1B1":  PinDef("43"),
        "P1B2":  PinDef("38"),
        "P1B3":  PinDef("34"),
        "P1B4":  PinDef("31"),
        "P1B7":  PinDef("42"),
        "P1B8":  PinDef("36"),
        "P1B9":  PinDef("32"),
        "P1B10": PinDef("28"),
    }
    clocks = {"CLK": ClockDef("CLK", 12.0)}
    return Board("icebreaker", "ice40", "up5k", "sg48", pins, clocks)


def _ulx3s() -> Board:
    pins = {
        "CLK_25MHZ": PinDef("G2"),
        "LED0": PinDef("B2"),
        "LED1": PinDef("C2"),
        "LED2": PinDef("C3"),
        "LED3": PinDef("D3"),
        "LED4": PinDef("F3"),
        "LED5": PinDef("G3"),
        "LED6": PinDef("H3"),
        "LED7": PinDef("H4"),
        "BTN_PWRn": PinDef("R1"),
        "BTN_F1":   PinDef("T1"),
        "BTN_F2":   PinDef("R18"),
        "BTN_UP":   PinDef("V1"),
        "BTN_DOWN": PinDef("U1"),
        "BTN_LEFT": PinDef("H16"),
        "BTN_RIGHT":PinDef("V17"),
        "UART_TX":  PinDef("L4"),
        "UART_RX":  PinDef("M1"),
    }
    clocks = {"CLK_25MHZ": ClockDef("CLK_25MHZ", 25.0)}
    return Board("ulx3s", "ecp5", "LFE5U-85F", "CABGA381", pins, clocks)


def _arty() -> Board:
    pins = {
        "CLK":  PinDef("E3", "LVCMOS33"),
        "LED0": PinDef("H5", "LVCMOS33"),
        "LED1": PinDef("J5", "LVCMOS33"),
        "LED2": PinDef("T9", "LVCMOS33"),
        "LED3": PinDef("T10", "LVCMOS33"),
        "BTN0": PinDef("D9", "LVCMOS33"),
        "BTN1": PinDef("C9", "LVCMOS33"),
        "BTN2": PinDef("B9", "LVCMOS33"),
        "BTN3": PinDef("B8", "LVCMOS33"),
        "SW0":  PinDef("A8", "LVCMOS33"),
        "SW1":  PinDef("C11", "LVCMOS33"),
        "SW2":  PinDef("C10", "LVCMOS33"),
        "SW3":  PinDef("A10", "LVCMOS33"),
        "UART_TX": PinDef("D10", "LVCMOS33"),
        "UART_RX": PinDef("A9", "LVCMOS33"),
    }
    clocks = {"CLK": ClockDef("CLK", 100.0)}
    return Board("arty", "xilinx", "xc7a35ticsg324-1L", "CSG324", pins, clocks)


def _de10nano() -> Board:
    pins = {
        "FPGA_CLK1_50": PinDef("V11"),
        "FPGA_CLK2_50": PinDef("Y13"),
        "FPGA_CLK3_50": PinDef("E11"),
        "LED0": PinDef("W15"),
        "LED1": PinDef("AA24"),
        "LED2": PinDef("V16"),
        "LED3": PinDef("V15"),
        "LED4": PinDef("AF26"),
        "LED5": PinDef("AE26"),
        "LED6": PinDef("Y16"),
        "LED7": PinDef("AA23"),
        "KEY0": PinDef("AH17"),
        "KEY1": PinDef("AH16"),
        "SW0":  PinDef("Y24"),
        "SW1":  PinDef("W24"),
        "SW2":  PinDef("W21"),
        "SW3":  PinDef("V21"),
    }
    clocks = {"FPGA_CLK1_50": ClockDef("FPGA_CLK1_50", 50.0)}
    return Board("de10nano", "intel", "5CSEBA6U23I7", "U484", pins, clocks)


# Registry of built-in boards
BOARDS: dict[str, Board] = {
    "icebreaker": _icebreaker(),
    "ulx3s":      _ulx3s(),
    "arty":       _arty(),
    "de10nano":   _de10nano(),
}


def get_board(name: str) -> Board:
    """Return a built-in board by name, or raise ``KeyError``."""
    try:
        return BOARDS[name.lower()]
    except KeyError:
        available = ", ".join(sorted(BOARDS))
        raise KeyError(f"Unknown board '{name}'. Available: {available}")
