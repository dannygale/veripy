"""Tests for GdbStub: RSP packet encoding, command dispatch, TCP server."""

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy.gdb_stub import GdbStub, _checksum, _decode, _encode
from veripy.soc import BusConfig, MemoryRegion, PlatformConfig, SocConfig
from veripy.soc_sim import SocSim


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_sim() -> SocSim:
    cfg = SocConfig(
        name='test',
        cpu=None,
        bus=BusConfig(),
        memory=[MemoryRegion(name='ram', base=0x0, size=0x1000, type='ram')],
        peripherals=[],
        platform=PlatformConfig(),
    )
    return SocSim(cfg)


class _FakeCpu:
    def __init__(self, num_regs: int = 33) -> None:
        self.regs = list(range(num_regs))
        self.steps = 0
        self.running = False

    def get_regs(self) -> list[int]:
        return list(self.regs)

    def set_regs(self, regs: list[int]) -> None:
        self.regs[:len(regs)] = regs

    def step(self) -> None:
        self.steps += 1

    def cont(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _send_recv(conn: socket.socket, payload: str) -> str:
    """Send a RSP packet and return the reply payload (strips $...#xx)."""
    conn.sendall(_encode(payload))
    data = b''
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        data += chunk
        # consume ACK
        if data.startswith(b'+'):
            data = data[1:]
        if b'#' in data:
            end = data.index(b'#')
            if end + 2 < len(data):
                break
    decoded = _decode(data)
    return decoded if decoded is not None else ''


# ── unit tests: packet helpers ─────────────────────────────────────────────────

class TestRspHelpers(unittest.TestCase):
    def test_checksum(self):
        self.assertEqual(_checksum('OK'), '9a')

    def test_encode_decode_roundtrip(self):
        for payload in ('OK', 'S05', 'g', 'm0,4', ''):
            encoded = _encode(payload)
            self.assertEqual(_decode(encoded), payload)

    def test_decode_malformed(self):
        self.assertIsNone(_decode(b'garbage'))
        self.assertIsNone(_decode(b'$OK'))  # missing checksum


# ── integration tests: TCP server ─────────────────────────────────────────────

class TestGdbStubServer(unittest.TestCase):
    def setUp(self):
        self.sim = _make_sim()
        self.cpu = _FakeCpu()
        self.port = _find_free_port()
        self.stub = GdbStub(self.sim, self.cpu, port=self.port)
        self.stub.start()
        time.sleep(0.05)
        self.conn = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        self.conn.settimeout(2)

    def tearDown(self):
        self.conn.close()
        self.stub.stop()

    def test_halt_reason(self):
        self.assertEqual(_send_recv(self.conn, '?'), 'S05')

    def test_read_regs(self):
        reply = _send_recv(self.conn, 'g')
        # 33 regs × 4 bytes × 2 hex chars = 264 chars
        self.assertEqual(len(reply), 33 * 4 * 2)
        # reg 0 should be 0x00000000 (little-endian)
        self.assertEqual(reply[:8], '00000000')
        # reg 1 should be 0x01000000 (value=1, little-endian)
        self.assertEqual(reply[8:16], '01000000')

    def test_write_regs(self):
        # Build G packet: 33 regs all set to 0xDEADBEEF
        val = (0xDEADBEEF).to_bytes(4, 'little').hex()
        payload = 'G' + val * 33
        self.assertEqual(_send_recv(self.conn, payload), 'OK')
        self.assertEqual(self.cpu.regs[0], 0xDEADBEEF)

    def test_read_one_reg(self):
        # p2 → reg[2] = 2 → little-endian 4 bytes
        reply = _send_recv(self.conn, 'p2')
        self.assertEqual(reply, (2).to_bytes(4, 'little').hex())

    def test_write_one_reg(self):
        val_hex = (0xCAFE).to_bytes(4, 'little').hex()
        self.assertEqual(_send_recv(self.conn, f'P5={val_hex}'), 'OK')
        self.assertEqual(self.cpu.regs[5], 0xCAFE)

    def test_read_mem(self):
        self.sim.write(0x100, 0xAABBCCDD, 4)
        reply = _send_recv(self.conn, 'm100,4')
        # little-endian bytes: DD CC BB AA
        self.assertEqual(reply, 'ddccbbaa')

    def test_write_mem(self):
        _send_recv(self.conn, 'M200,4:11223344')
        val = self.sim.read(0x200, 4)
        self.assertEqual(val, 0x44332211)

    def test_step(self):
        self.assertEqual(_send_recv(self.conn, 's'), 'S05')
        self.assertEqual(self.cpu.steps, 1)

    def test_continue(self):
        reply = _send_recv(self.conn, 'c')
        self.assertEqual(reply, 'S05')
        self.assertTrue(self.cpu.running)

    def test_qsupported(self):
        reply = _send_recv(self.conn, 'qSupported')
        self.assertIn('PacketSize', reply)

    def test_qattached(self):
        self.assertEqual(_send_recv(self.conn, 'qAttached'), '1')

    def test_qC(self):
        self.assertEqual(_send_recv(self.conn, 'qC'), 'QC1')

    def test_thread_info(self):
        self.assertEqual(_send_recv(self.conn, 'qfThreadInfo'), 'm1')
        self.assertEqual(_send_recv(self.conn, 'qsThreadInfo'), 'l')

    def test_unsupported_returns_empty(self):
        self.assertEqual(_send_recv(self.conn, 'vMustReplyEmpty'), '')

    def test_context_manager(self):
        port2 = _find_free_port()
        with GdbStub(_make_sim(), _FakeCpu(), port=port2) as stub:
            time.sleep(0.05)
            c = socket.create_connection(('127.0.0.1', port2), timeout=1)
            c.settimeout(1)
            self.assertEqual(_send_recv(c, '?'), 'S05')
            c.close()


if __name__ == '__main__':
    unittest.main()
