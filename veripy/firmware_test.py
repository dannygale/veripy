"""FirmwareTestCase: ELF-based CPU testing against behavioral and RTL paths."""

import glob
import unittest

from .soc_sim import FlatMemory, SocSim
from .soc import SocConfig

TOHOST_ADDR = 0x1000  # standard riscv-tests tohost address


class FirmwareTestCase(unittest.TestCase):
    """Test case for ELF-based CPU testing.

    Runs firmware through a behavioral SocSim and optionally a csim RTL model,
    comparing results. Supports the riscv-tests tohost/fromhost halt protocol.

    Usage::

        class TestCompliance(FirmwareTestCase):
            def create_cpu(self): return my_pipeline()
            def create_soc(self): return SocConfig.minimal(ram_size=0x10000)

            def test_add(self):
                self.load_elf('tests/rv32ui-p-add')
                self.run_until_halt(timeout=20_000)
                self.assertEqual(self.tohost(), 1)

            def test_rv32ui(self):
                self.run_arch_suite('tests/rv32ui-p-*.elf')
    """

    tohost_addr: int = TOHOST_ADDR
    backend: str = 'behavioral'  # 'behavioral' | 'rtl' | 'check'

    def create_cpu(self):
        raise NotImplementedError

    def create_soc(self) -> SocConfig:
        raise NotImplementedError

    def setUp(self):
        self._soc = SocSim(self.create_soc())
        self._tohost_val: int | None = None
        self._halted = False

    # ── public API ────────────────────────────────────────────────────────────

    def load_elf(self, path: str) -> int:
        """Load ELF into SoC memory. Returns entry point."""
        self._tohost_val = None
        self._halted = False
        return self._soc.load_elf(path)

    def run_until_halt(self, timeout: int = 100_000) -> None:
        """Run behavioral CPU until tohost write or timeout.

        Subclasses must implement _step() to advance the CPU one cycle and
        call self._soc.read()/write() for bus access. Alternatively, override
        this method entirely for a custom execution loop.
        """
        for _ in range(timeout):
            self._step()
            val = self._soc.memory.read(self.tohost_addr, 4)
            if val != 0:
                self._tohost_val = val
                self._halted = True
                return
        self.fail(f'run_until_halt: timeout after {timeout} cycles')

    def tohost(self) -> int:
        """Return the tohost value written by firmware. 1 = pass (riscv-tests)."""
        if self._tohost_val is None:
            self.fail('tohost() called before run_until_halt() completed')
        return self._tohost_val

    def run_arch_suite(self, pattern: str) -> None:
        """Run all ELFs matching glob pattern as subtests.

        Each ELF is expected to write 1 to tohost on success (riscv-tests convention).
        """
        elfs = sorted(glob.glob(pattern))
        if not elfs:
            self.skipTest(f'No ELFs found matching {pattern!r}')
        for elf in elfs:
            with self.subTest(elf=elf):
                self.setUp()
                self.load_elf(elf)
                self.run_until_halt()
                self.assertEqual(
                    self.tohost(), 1,
                    f'{elf}: expected tohost=1 (pass), got {self._tohost_val}')

    # ── override point ────────────────────────────────────────────────────────

    def _step(self) -> None:
        """Advance CPU one cycle. Override to drive your CPU model.

        Use self._soc.read(addr, size) and self._soc.write(addr, val, size)
        for memory/peripheral access.
        """
        raise NotImplementedError(
            'Override _step() to advance your CPU model one cycle, '
            'or override run_until_halt() entirely.')
