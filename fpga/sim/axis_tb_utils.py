"""
Shared cocotb AXI4-Stream test utilities for QKD Phase-BB84.

Copied from FPGA-GGX-Sampler with domain-specific helpers removed.
Core AXISDriver/AXISMonitor logic is unchanged.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge, ReadOnly
from cocotb.utils import get_sim_time as gst
from cocotb_bus.drivers import BusDriver
from cocotb_bus.monitors import BusMonitor


class AXISMonitor(BusMonitor):
    """
    Monitors AXI4-Stream bus handshakes.
    """
    transactions = 0

    def __init__(self, dut, name, clk, callback=None):
        self._signals = ['axis_tvalid', 'axis_tready', 'axis_tlast', 'axis_tdata', 'axis_tstrb']
        BusMonitor.__init__(self, dut, name, clk, callback=callback)
        self.clock = clk
        self.transactions = 0

    async def _monitor_recv(self):
        rising_edge = RisingEdge(self.clock)
        falling_edge = FallingEdge(self.clock)
        read_only = ReadOnly()
        while True:
            await rising_edge
            await falling_edge
            await read_only
            valid = self.bus.axis_tvalid.value
            ready = self.bus.axis_tready.value
            if valid and ready:
                self.transactions += 1
                self._recv(self.bus.axis_tdata.value)


class AXISDriver(BusDriver):
    """
    Drives AXI4-Stream bus as either Master ('M') or Slave ('S').

    Transaction types:
      Master:
        {"type": "write_single", "contents": {"data": <int>, "last": <0|1>}}
        {"type": "write_burst",  "contents": {"data": [<int>, ...]}}
        {"type": "pause",        "duration": <int>}
      Slave:
        {"type": "read_single"}
        {"type": "read_burst",   "duration": <int>}
        {"type": "pause",        "duration": <int>}
        {"type": "ready_high",   "duration": <int>}
    """

    def __init__(self, dut, name, clk, role="M"):
        self._signals = ['axis_tvalid', 'axis_tready', 'axis_tlast', 'axis_tdata', 'axis_tstrb']
        BusDriver.__init__(self, dut, name, clk)
        self.clock = clk
        if role == 'M':
            self.role = role
            self.bus.axis_tdata.value = 0
            self.bus.axis_tstrb.value = 0
            self.bus.axis_tlast.value = 0
            self.bus.axis_tvalid.value = 0
        elif role == 'S':
            self.role = role
            self.bus.axis_tready.value = 0
        else:
            raise ValueError("role can only be 'M' or 'S'")

    async def _driver_send(self, value, sync=True):
        rising_edge = RisingEdge(self.clock)
        falling_edge = FallingEdge(self.clock)
        read_only = ReadOnly()
        if self.role == 'M':
            if value.get("type") == "write_single":
                await falling_edge
                self.bus.axis_tdata.value = value.get('contents').get('data')
                self.bus.axis_tstrb.value = 0xF
                self.bus.axis_tlast.value = value.get('contents').get('last')
                self.bus.axis_tvalid.value = 1
                await read_only
                if self.bus.axis_tready.value == 0:
                    await RisingEdge(self.bus.axis_tready)
                await rising_edge
            elif value.get("type") == "pause":
                await falling_edge
                self.bus.axis_tvalid.value = 0
                await ClockCycles(self.clock, value.get("duration", 1))
            elif value.get("type") == "write_burst":
                data = value.get("contents").get("data")
                for i in range(len(data)):
                    await falling_edge
                    self.bus.axis_tdata.value = int(data[i])
                    if i == len(data) - 1:
                        self.bus.axis_tlast.value = 1
                    else:
                        self.bus.axis_tlast.value = 0
                    self.bus.axis_tvalid.value = 1
                    if self.bus.axis_tready.value == 0:
                        await RisingEdge(self.bus.axis_tready)
                    await rising_edge
                self.bus.axis_tvalid.value = 0
                self.bus.axis_tlast.value = 0
        elif self.role == 'S':
            if value.get("type") == "pause":
                await falling_edge
                self.bus.axis_tready.value = 0
                await ClockCycles(self.clock, value.get("duration", 1))
            elif value.get("type") == "read_single":
                await falling_edge
                self.bus.axis_tready.value = 1
                await read_only
                if self.bus.axis_tvalid.value == 0:
                    await RisingEdge(self.bus.axis_tvalid)
                await rising_edge
                self.bus.axis_tready.value = 0
            elif value.get("type") == "read_burst":
                for i in range(value.get("duration", 1)):
                    await falling_edge
                    self.bus.axis_tready.value = 1
                    await read_only
                    if self.bus.axis_tvalid.value == 0:
                        await RisingEdge(self.bus.axis_tvalid)
                    await rising_edge
                self.bus.axis_tready.value = 0
            elif value.get("type") == "ready_high":
                await falling_edge
                self.bus.axis_tready.value = 1
                await ClockCycles(self.clock, value.get("duration", 1))
                self.bus.axis_tready.value = 0


async def reset(clk, rst, cycles_held=3, polarity=1):
    """Assert reset for cycles_held clock cycles."""
    rst.value = polarity
    await ClockCycles(clk, cycles_held)
    rst.value = not polarity
