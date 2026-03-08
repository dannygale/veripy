"""Tests for veripy.fpga: Board, ConstraintSet, built-in boards."""

import unittest

from veripy.fpga import (
    Board, PinDef, ClockDef, ConstraintSet,
    BOARDS, get_board,
)


class TestPinDef(unittest.TestCase):
    def test_defaults(self):
        p = PinDef("35")
        self.assertEqual(p.loc, "35")
        self.assertIsNone(p.io_std)

    def test_with_std(self):
        p = PinDef("E3", "LVCMOS33")
        self.assertEqual(p.io_std, "LVCMOS33")


class TestConstraintSetPCF(unittest.TestCase):
    def setUp(self):
        self.board = BOARDS["icebreaker"]

    def test_emit_pcf_basic(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "35")
        cs.pin("led", "11")
        pcf = cs.emit_pcf()
        self.assertIn("set_io clk 35", pcf)
        self.assertIn("set_io led 11", pcf)

    def test_emit_pcf_with_clock(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "35")
        cs.clock("clk", 12.0)
        pcf = cs.emit_pcf()
        self.assertIn("set_frequency clk 12.0", pcf)

    def test_emit_dispatches_to_pcf(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "35")
        self.assertEqual(cs.emit(), cs.emit_pcf())

    def test_constraint_ext(self):
        cs = ConstraintSet(self.board)
        self.assertEqual(cs.constraint_ext(), ".pcf")


class TestConstraintSetLPF(unittest.TestCase):
    def setUp(self):
        self.board = BOARDS["ulx3s"]

    def test_emit_lpf_basic(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "G2")
        lpf = cs.emit_lpf()
        self.assertIn('LOCATE COMP "clk" SITE "G2"', lpf)
        self.assertIn('IOBUF PORT "clk" IO_TYPE=LVCMOS33', lpf)

    def test_emit_lpf_clock(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "G2")
        cs.clock("clk", 25.0)
        lpf = cs.emit_lpf()
        self.assertIn('FREQUENCY PORT "clk" 25.000 MHZ', lpf)

    def test_constraint_ext(self):
        cs = ConstraintSet(self.board)
        self.assertEqual(cs.constraint_ext(), ".lpf")


class TestConstraintSetXDC(unittest.TestCase):
    def setUp(self):
        self.board = BOARDS["arty"]

    def test_emit_xdc_basic(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "E3", "LVCMOS33")
        xdc = cs.emit_xdc()
        self.assertIn("set_property PACKAGE_PIN E3 [get_ports {clk}]", xdc)
        self.assertIn("set_property IOSTANDARD LVCMOS33 [get_ports {clk}]", xdc)

    def test_emit_xdc_clock(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "E3", "LVCMOS33")
        cs.clock("clk", 100.0)
        xdc = cs.emit_xdc()
        self.assertIn("create_clock -period 10.000 -name clk [get_ports {clk}]", xdc)

    def test_emit_dispatches_to_xdc(self):
        cs = ConstraintSet(self.board)
        cs.pin("clk", "E3")
        self.assertEqual(cs.emit(), cs.emit_xdc())

    def test_constraint_ext(self):
        cs = ConstraintSet(self.board)
        self.assertEqual(cs.constraint_ext(), ".xdc")


class TestBoardConstraintSet(unittest.TestCase):
    def test_icebreaker_port_map(self):
        board = BOARDS["icebreaker"]
        cs = board.constraint_set({"clk": "CLK", "led": "LED_R_N"})
        pcf = cs.emit_pcf()
        self.assertIn("set_io clk 35", pcf)
        self.assertIn("set_io led 11", pcf)
        # Clock auto-detected
        self.assertIn("set_frequency clk 12.0", pcf)

    def test_arty_port_map(self):
        board = BOARDS["arty"]
        cs = board.constraint_set({"clk": "CLK", "led": "LED0"})
        xdc = cs.emit_xdc()
        self.assertIn("set_property PACKAGE_PIN E3 [get_ports {clk}]", xdc)
        self.assertIn("set_property PACKAGE_PIN H5 [get_ports {led}]", xdc)

    def test_unknown_pin_raises(self):
        board = BOARDS["icebreaker"]
        with self.assertRaises(KeyError):
            board.constraint_set({"clk": "NONEXISTENT_PIN"})


class TestBuiltinBoards(unittest.TestCase):
    def test_all_boards_present(self):
        for name in ("icebreaker", "ulx3s", "arty", "de10nano"):
            self.assertIn(name, BOARDS)

    def test_icebreaker_attrs(self):
        b = BOARDS["icebreaker"]
        self.assertEqual(b.family, "ice40")
        self.assertEqual(b.part, "up5k")
        self.assertIn("CLK", b.pins)
        self.assertIn("CLK", b.clocks)
        self.assertAlmostEqual(b.clocks["CLK"].freq_mhz, 12.0)

    def test_ulx3s_attrs(self):
        b = BOARDS["ulx3s"]
        self.assertEqual(b.family, "ecp5")
        self.assertIn("CLK_25MHZ", b.pins)

    def test_arty_attrs(self):
        b = BOARDS["arty"]
        self.assertEqual(b.family, "xilinx")
        self.assertIn("CLK", b.pins)
        self.assertEqual(b.pins["CLK"].io_std, "LVCMOS33")

    def test_de10nano_attrs(self):
        b = BOARDS["de10nano"]
        self.assertEqual(b.family, "intel")
        self.assertIn("FPGA_CLK1_50", b.pins)

    def test_get_board(self):
        b = get_board("icebreaker")
        self.assertEqual(b.name, "icebreaker")

    def test_get_board_case_insensitive(self):
        b = get_board("ICEBREAKER")
        self.assertEqual(b.name, "icebreaker")

    def test_get_board_unknown(self):
        with self.assertRaises(KeyError):
            get_board("nonexistent_board")


class TestUnsupportedFamily(unittest.TestCase):
    def test_emit_unknown_family(self):
        board = Board("custom", "unknown_vendor", "XYZ123")
        cs = ConstraintSet(board)
        cs.pin("clk", "V11")
        with self.assertRaises(ValueError):
            cs.emit()


if __name__ == "__main__":
    unittest.main()
