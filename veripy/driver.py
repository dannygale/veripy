"""Transaction-level protocol driver base class.

Subclass ``Driver`` and override :meth:`send` as a generator that yields
time delays and ``until()`` conditions to drive transactions onto a DUT.

Example usage in a testbench::

    drv = SpiDriver(sim, dut)

    @sim.initial
    def stim():
        yield from drv.send({'data': 0xA5})
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
