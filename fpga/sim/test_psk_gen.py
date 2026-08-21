#!/usr/bin/env python3
"""
Cocotb testbench for axis_psk_gen.

Tests:
  1. Each phase_select value produces the correct I/Q output
  2. Phase switching propagates within 2 clock cycles
  3. Backpressure via tready deassertion
  4. Enable gating
"""

import cocotb
import os
import sys
from pathlib import Path
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge, ReadOnly
from cocotb_tools.runner import get_runner

# Add sim dir to path for shared utilities
sys.path.insert(0, str(Path(__file__).resolve().parent))
from axis_tb_utils import AXISMonitor, AXISDriver, reset

test_file = os.path.basename(__file__).replace(".py", "")
proj_path = Path(__file__).resolve().parent.parent

# Expected I/Q values (matching axis_psk_gen.sv constants)
POS_FULL = 30000
NEG_FULL = -30000 & 0xFFFF  # 16-bit two's complement
ZERO = 0

# phase_select → (I, Q) expected values (16-bit unsigned representation)
PHASE_MAP = {
    0b00: (POS_FULL,  ZERO),       # 0
    0b01: (ZERO,      POS_FULL),   # pi/2
    0b10: (NEG_FULL,  ZERO),       # pi
    0b11: (ZERO,      NEG_FULL),   # 3pi/2
}


def extract_iq(tdata_val, sample_idx=0):
    """Extract I and Q from a 64-bit tdata word at the given sample slot."""
    val = int(tdata_val)
    sample = (val >> (sample_idx * 32)) & 0xFFFFFFFF
    i_val = (sample >> 16) & 0xFFFF
    q_val = sample & 0xFFFF
    return i_val, q_val


@cocotb.test()
async def test_phase_states(dut):
    """Verify all 4 phase states produce correct I/Q output."""
    cocotb.start_soon(Clock(dut.aclk, 3, units="ns").start())  # ~333 MHz
    dut.enable.value = 0
    dut.phase_select.value = 0
    dut.m_axis_tready.value = 1
    await reset(dut.aclk, dut.aresetn, cycles_held=5, polarity=0)

    dut.enable.value = 1

    for phase_sel, (exp_i, exp_q) in PHASE_MAP.items():
        dut.phase_select.value = phase_sel
        # Wait for registered output to propagate (1 cycle register + margin)
        await ClockCycles(dut.aclk, 3)
        await FallingEdge(dut.aclk)
        await ReadOnly()

        # Check both sample slots are identical
        for slot in range(2):
            act_i, act_q = extract_iq(dut.m_axis_tdata.value, slot)
            assert act_i == exp_i, \
                f"phase_sel={phase_sel} slot={slot}: I expected {exp_i:#06x}, got {act_i:#06x}"
            assert act_q == exp_q, \
                f"phase_sel={phase_sel} slot={slot}: Q expected {exp_q:#06x}, got {act_q:#06x}"

        # Verify tvalid is high
        assert dut.m_axis_tvalid.value == 1, "tvalid should be high when enabled"

        # Return to a writable phase before next iteration
        await RisingEdge(dut.aclk)

    dut._log.info("All 4 phase states verified OK")


@cocotb.test()
async def test_phase_switch_latency(dut):
    """Verify phase switches propagate within 2 clock cycles."""
    cocotb.start_soon(Clock(dut.aclk, 3, units="ns").start())
    dut.enable.value = 0
    dut.phase_select.value = 0
    dut.m_axis_tready.value = 1
    await reset(dut.aclk, dut.aresetn, cycles_held=5, polarity=0)

    dut.enable.value = 1
    await ClockCycles(dut.aclk, 3)

    # Switch from phase 0 to phase pi
    dut.phase_select.value = 0b10
    # After 2 clocks the output should reflect the new phase
    await ClockCycles(dut.aclk, 2)
    await FallingEdge(dut.aclk)
    await ReadOnly()

    act_i, act_q = extract_iq(dut.m_axis_tdata.value, 0)
    assert act_i == NEG_FULL, f"Phase switch latency: I expected {NEG_FULL:#06x}, got {act_i:#06x}"
    assert act_q == ZERO, f"Phase switch latency: Q expected {ZERO:#06x}, got {act_q:#06x}"
    dut._log.info("Phase switch latency <= 2 cycles OK")


@cocotb.test()
async def test_enable_gating(dut):
    """Verify tvalid is low when disabled."""
    cocotb.start_soon(Clock(dut.aclk, 3, units="ns").start())
    dut.enable.value = 0
    dut.phase_select.value = 0
    dut.m_axis_tready.value = 1
    await reset(dut.aclk, dut.aresetn, cycles_held=5, polarity=0)

    # Should be invalid when disabled
    await ClockCycles(dut.aclk, 3)
    await FallingEdge(dut.aclk)
    await ReadOnly()
    assert dut.m_axis_tvalid.value == 0, "tvalid should be 0 when disabled"

    # Return to writable phase before driving
    await RisingEdge(dut.aclk)

    # Enable and check
    dut.enable.value = 1
    await ClockCycles(dut.aclk, 2)
    await FallingEdge(dut.aclk)
    await ReadOnly()
    assert dut.m_axis_tvalid.value == 1, "tvalid should be 1 when enabled"

    # Return to writable phase before driving
    await RisingEdge(dut.aclk)

    # Disable again
    dut.enable.value = 0
    await ClockCycles(dut.aclk, 2)
    await FallingEdge(dut.aclk)
    await ReadOnly()
    assert dut.m_axis_tvalid.value == 0, "tvalid should be 0 after disable"
    dut._log.info("Enable gating OK")


@cocotb.test()
async def test_backpressure(dut):
    """Verify module handles tready deassertion gracefully."""
    received = []

    def monitor_cb(transaction):
        received.append(int(transaction))

    cocotb.start_soon(Clock(dut.aclk, 3, units="ns").start())
    dut.enable.value = 0
    dut.phase_select.value = 0b01  # pi/2
    dut.m_axis_tready.value = 0
    await reset(dut.aclk, dut.aresetn, cycles_held=5, polarity=0)

    outm = AXISMonitor(dut, 'm', dut.aclk, callback=monitor_cb)

    dut.enable.value = 1

    # Hold tready low for 10 cycles — tvalid should stay asserted,
    # but monitor should see no transactions
    await ClockCycles(dut.aclk, 10)
    assert len(received) == 0, "Should not receive data when tready=0"
    assert dut.m_axis_tvalid.value == 1, "tvalid should still be high under backpressure"

    # Release backpressure — should start receiving
    dut.m_axis_tready.value = 1
    await ClockCycles(dut.aclk, 10)
    assert len(received) > 0, "Should receive data once tready goes high"
    dut._log.info(f"Backpressure test OK: received {len(received)} transactions after release")


def psk_gen_runner():
    sim = os.getenv("SIM", "icarus")
    sources = [proj_path / "hdl" / "axis_psk_gen.sv"]
    build_test_args = ["-Wall"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="axis_psk_gen",
        always=True,
        build_args=build_test_args,
        parameters={"SAMPLES_PER_BEAT": 2, "SAMPLE_WIDTH": 16},
        timescale=('1ns', '1ps'),
        waves=True
    )
    runner.test(
        hdl_toplevel="axis_psk_gen",
        test_module=test_file,
        waves=True
    )


if __name__ == "__main__":
    psk_gen_runner()
