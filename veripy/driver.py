"""Transaction-level protocol driver base class.

Subclass ``Driver`` and override :meth:`send` / :meth:`recv` as generators
that yield time delays and ``until()`` conditions to drive transactions.

Example usage in a testbench::

    drv = SpiDriver(sim, dut)

    @sim.initial
    def stim():
        yield from drv.send({'data': 0xA5})
        result = yield from drv.recv()
"""


class Driver:
    """Base class for transaction-level protocol drivers.

    Parameters
    ----------
    engine : SimEngine
        The simulation engine (used for ``engine.time``, ``engine.fork``, etc.).
    mod : Module
        The DUT module whose signals this driver manipulates.
    """

    def __init__(self, engine, mod):
        self.engine = engine
        self.mod = mod

    def send(self, txn):
        """Generator: drive *txn* onto the bus.

        Override in subclass.  Yield integer delays or ``until()`` sentinels
        exactly as you would inside a ``@sim.initial`` block.
        """
        raise NotImplementedError("subclass must implement send()")

    def recv(self):
        """Generator: wait for and return a received transaction.

        Override in subclass for bidirectional protocols.
        """
        raise NotImplementedError("subclass must implement recv()")

    def reset(self):
        """Generator: perform a protocol-level reset sequence.

        Override in subclass if the protocol has a reset handshake.
        """
        raise NotImplementedError("subclass must implement reset()")
