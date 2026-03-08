"""GDB Remote Serial Protocol (RSP) stub for firmware co-simulation.

GdbStub wraps a SocSim and a CPU register interface, serving GDB RSP over TCP.
GDB connects with: ``target remote localhost:<port>``

CPU interface (duck-typed):
    get_regs() -> list[int]   — return all integer registers (e.g. 33 for RV32I: x0-x31 + pc)
    set_regs(regs: list[int]) — write all registers back
    step()                    — execute one instruction, update regs/memory
    cont()                    — run until a breakpoint or explicit stop(); returns when halted
    stop()                    — request halt (called from another thread or signal)
"""

from __future__ import annotations

import socket
import threading
from typing import Protocol, runtime_checkable

from .soc_sim import SocSim


@runtime_checkable
class CpuInterface(Protocol):
    def get_regs(self) -> list[int]: ...
    def set_regs(self, regs: list[int]) -> None: ...
    def step(self) -> None: ...
    def cont(self) -> None: ...
    def stop(self) -> None: ...


# ── RSP packet helpers ─────────────────────────────────────────────────────────

def _checksum(data: str) -> str:
    return f"{sum(ord(c) for c in data) & 0xFF:02x}"


def _encode(data: str) -> bytes:
    return f"${data}#{_checksum(data)}".encode()


def _decode(raw: bytes) -> str | None:
    """Extract payload from a $...#xx packet; return None if malformed."""
    s = raw.decode(errors='replace')
    start = s.find('$')
    end = s.find('#', start + 1)
    if start == -1 or end == -1 or end + 2 > len(s):
        return None
    return s[start + 1:end]


# ── GdbStub ────────────────────────────────────────────────────────────────────

class GdbStub:
    """GDB RSP stub.

    Args:
        sim:  SocSim instance (provides memory read/write).
        cpu:  Object implementing CpuInterface (register access + step/cont).
        port: TCP port to listen on (default 1234).
        reg_width: Register width in bytes (4 for RV32, 8 for RV64).
    """

    def __init__(
        self,
        sim: SocSim,
        cpu: CpuInterface,
        port: int = 1234,
        reg_width: int = 4,
    ) -> None:
        self.sim = sim
        self.cpu = cpu
        self.port = port
        self.reg_width = reg_width
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── server lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the RSP server in a background thread."""
        self._stop_event.clear()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', self.port))
        self._server.listen(1)
        self._server.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the RSP server."""
        self._stop_event.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def __enter__(self) -> 'GdbStub':
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ── server loop ────────────────────────────────────────────────────────────

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = self._server.accept()  # type: ignore[union-attr]
            except (socket.timeout, OSError):
                continue
            try:
                self._handle(conn)
            finally:
                conn.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        buf = b''
        while not self._stop_event.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while buf:
                # ACK/NAK passthrough
                if buf[0:1] in (b'+', b'-'):
                    buf = buf[1:]
                    continue
                # Ctrl-C interrupt
                if buf[0:1] == b'\x03':
                    self.cpu.stop()
                    buf = buf[1:]
                    conn.sendall(b'+')
                    conn.sendall(_encode('S05'))
                    continue
                # Packet: $...#xx
                start = buf.find(b'$')
                if start == -1:
                    buf = b''
                    break
                end = buf.find(b'#', start + 1)
                if end == -1 or end + 2 >= len(buf):
                    break  # wait for more data
                packet = buf[start:end + 3]
                buf = buf[end + 3:]
                conn.sendall(b'+')  # ACK
                payload = _decode(packet)
                if payload is None:
                    conn.sendall(_encode('E01'))
                    continue
                reply = self._dispatch(payload)
                if reply is not None:
                    conn.sendall(_encode(reply))

    # ── RSP command dispatch ───────────────────────────────────────────────────

    def _dispatch(self, cmd: str) -> str | None:
        if cmd == '?':
            return 'S05'  # SIGTRAP — halted

        if cmd == 'g':
            return self._read_regs()

        if cmd.startswith('G'):
            return self._write_regs(cmd[1:])

        if cmd.startswith('m'):
            return self._read_mem(cmd[1:])

        if cmd.startswith('M'):
            return self._write_mem(cmd[1:])

        if cmd.startswith('p'):
            return self._read_one_reg(cmd[1:])

        if cmd.startswith('P'):
            return self._write_one_reg(cmd[1:])

        if cmd == 's':
            self.cpu.step()
            return 'S05'

        if cmd.startswith('s'):
            # s<addr> — step from address (ignore addr, just step)
            self.cpu.step()
            return 'S05'

        if cmd == 'c' or cmd.startswith('c'):
            self.cpu.cont()
            return 'S05'

        if cmd == 'k':
            self.cpu.stop()
            return None  # no reply for kill

        if cmd.startswith('q'):
            return self._query(cmd)

        if cmd.startswith('v'):
            return ''  # unsupported vPacket

        return ''  # empty = unsupported

    # ── register access ────────────────────────────────────────────────────────

    def _read_regs(self) -> str:
        regs = self.cpu.get_regs()
        w = self.reg_width
        return ''.join(
            r.to_bytes(w, 'little').hex() for r in regs
        )

    def _write_regs(self, hex_data: str) -> str:
        w = self.reg_width
        regs = []
        for i in range(0, len(hex_data), w * 2):
            chunk = hex_data[i:i + w * 2]
            if len(chunk) < w * 2:
                break
            regs.append(int.from_bytes(bytes.fromhex(chunk), 'little'))
        self.cpu.set_regs(regs)
        return 'OK'

    def _read_one_reg(self, arg: str) -> str:
        try:
            idx = int(arg, 16)
        except ValueError:
            return 'E01'
        regs = self.cpu.get_regs()
        if idx >= len(regs):
            return 'E01'
        return regs[idx].to_bytes(self.reg_width, 'little').hex()

    def _write_one_reg(self, arg: str) -> str:
        try:
            idx_s, val_s = arg.split('=', 1)
            idx = int(idx_s, 16)
            val = int.from_bytes(bytes.fromhex(val_s), 'little')
        except (ValueError, KeyError):
            return 'E01'
        regs = self.cpu.get_regs()
        if idx >= len(regs):
            return 'E01'
        regs[idx] = val
        self.cpu.set_regs(regs)
        return 'OK'

    # ── memory access ──────────────────────────────────────────────────────────

    def _read_mem(self, arg: str) -> str:
        try:
            addr_s, len_s = arg.split(',', 1)
            addr = int(addr_s, 16)
            length = int(len_s, 16)
        except ValueError:
            return 'E01'
        result = []
        for i in range(length):
            result.append(f"{self.sim.read(addr + i, 1):02x}")
        return ''.join(result)

    def _write_mem(self, arg: str) -> str:
        try:
            loc, hex_data = arg.split(':', 1)
            addr_s, len_s = loc.split(',', 1)
            addr = int(addr_s, 16)
            length = int(len_s, 16)
        except ValueError:
            return 'E01'
        data = bytes.fromhex(hex_data[:length * 2])
        for i, byte in enumerate(data):
            self.sim.write(addr + i, byte, 1)
        return 'OK'

    # ── qPackets ───────────────────────────────────────────────────────────────

    def _query(self, cmd: str) -> str:
        if cmd == 'qSupported':
            return 'PacketSize=4000'
        if cmd == 'qAttached':
            return '1'
        if cmd == 'qC':
            return 'QC1'  # current thread id = 1
        if cmd == 'qfThreadInfo':
            return 'm1'
        if cmd == 'qsThreadInfo':
            return 'l'
        return ''
