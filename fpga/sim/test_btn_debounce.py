#!/usr/bin/env python3
"""
Cocotb testbench for btn_debounce module.

Tests:
  1. Clean press produces a single-cycle pulse
  2. Bouncy input is rejected (no pulse during bounce window)
  3. Multiple buttons work independently
"""

import cocotb
import os
import sys
from pathlib import Path
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from cocotb.runner import get_runner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axis_tb_utils import reset

test_file = os.path.basename(__file__).replace(".py", "")
proj_path = Path(__file__).resolve().parent.parent

# With CLK_FREQ_HZ=10000 and DEBOUNCE_MS=20:
# COUNT_MAX = (10000/1000)*20 = 200 cycles
# Total latency: 2 (sync) + 200 (debounce) + 1 (latch) + 1 (edge) ≈ 204 cycles
SETTLE_CYCLES = 250  # generous window to capture pulse


async def count_pulses(dut, cycles):
    """Count btn_pulse assertions over N clock cycles. Returns per-button counts.
    Uses FallingEdge so callers can write signals immediately after."""
    counts = [0] * 4
    for _ in range(cycles):
        await FallingEdge(dut.clk)
        pval = int(dut.btn_pulse.value)
        for b in range(4):
            if pval & (1 << b):
                counts[b] += 1
    return counts


@cocotb.test()
async def test_clean_press(dut):
    """A clean button press should produce exactly one pulse."""
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    dut.btn_raw.value = 0
    dut.resetn.value = 0
    await ClockCycles(dut.clk, 5)
    dut.resetn.value = 1
    await ClockCycles(dut.clk, 5)

    # Press button 0 and monitor for pulse during the settle window
    dut.btn_raw.value = 0b0001
    counts = await count_pulses(dut, SETTLE_CYCLES)

    assert counts[0] == 1, f"Button 0: expected 1 pulse, got {counts[0]}"
    assert counts[1] == 0, f"Button 1: expected 0 pulses, got {counts[1]}"

    # Hold for a while — no additional pulses should appear
    extra = await count_pulses(dut, 50)
    assert extra[0] == 0, f"Extra pulses detected while held: {extra[0]}"

    # Release and verify no pulse on release (we only detect rising edge)
    dut.btn_raw.value = 0
    release_counts = await count_pulses(dut, SETTLE_CYCLES)
    assert release_counts[0] == 0, f"Unexpected pulse on release: {release_counts[0]}"

    dut._log.info("Clean press test passed")


@cocotb.test()
async def test_bounce_rejection(dut):
    """Bouncing input should not produce a pulse until stable."""
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    dut.btn_raw.value = 0
    dut.resetn.value = 0
    await ClockCycles(dut.clk, 5)
    dut.resetn.value = 1
    await ClockCycles(dut.clk, 5)

    # Simulate bounce: toggle rapidly for less than debounce window
    # Each toggle resets the debounce counter, so no pulse should fire
    bounce_pulses = [0] * 4
    for _ in range(10):
        dut.btn_raw.value = 0b0001
        c = await count_pulses(dut, 3)
        for b in range(4):
            bounce_pulses[b] += c[b]
        dut.btn_raw.value = 0b0000
        c = await count_pulses(dut, 3)
        for b in range(4):
            bounce_pulses[b] += c[b]

    assert bounce_pulses[0] == 0, f"Pulse during bounce: {bounce_pulses[0]}"

    # Now hold steady (pressed) — should eventually produce a pulse
    dut.btn_raw.value = 0b0001
    stable_counts = await count_pulses(dut, SETTLE_CYCLES)
    assert stable_counts[0] == 1, f"Expected 1 pulse after stable, got {stable_counts[0]}"

    dut._log.info("Bounce rejection test passed")


@cocotb.test()
async def test_independent_buttons(dut):
    """Multiple buttons work independently."""
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    dut.btn_raw.value = 0
    dut.resetn.value = 0
    await ClockCycles(dut.clk, 5)
    dut.resetn.value = 1
    await ClockCycles(dut.clk, 5)

    # Press button 0 and button 2 simultaneously
    dut.btn_raw.value = 0b0101
    counts = await count_pulses(dut, SETTLE_CYCLES)

    assert counts[0] == 1, f"Button 0: expected 1 pulse, got {counts[0]}"
    assert counts[1] == 0, f"Button 1: expected 0 pulses, got {counts[1]}"
    assert counts[2] == 1, f"Button 2: expected 1 pulse, got {counts[2]}"
    assert counts[3] == 0, f"Button 3: expected 0 pulses, got {counts[3]}"

    dut._log.info("Independent buttons test passed")


def btn_debounce_runner():
    sim = os.getenv("SIM", "icarus")
    sources = [
        proj_path / "hdl" / "btn_debounce.sv",
    ]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="btn_debounce",
        always=True,
        build_args=["-Wall"],
        parameters={
            "CLK_FREQ_HZ": 10000,
            "DEBOUNCE_MS": 20,
            "N_BUTTONS": 4,
        },
        timescale=('1ns', '1ps'),
        waves=True
    )
    runner.test(
        hdl_toplevel="btn_debounce",
        test_module=test_file,
        waves=True
    )


if __name__ == "__main__":
    btn_debounce_runner()
