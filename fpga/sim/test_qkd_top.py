#!/usr/bin/env python3
"""
Cocotb testbench for qkd_top_wrapper.

Tests:
  1. Switch mode: DIP switches control phase output and LEDs
  2. Register mode: AXI-Lite writes staged phases, PHASE_APPLY latches atomically
  3. Mode mux: SW3 toggles between switch and register control
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

# Register offsets (byte addresses)
REG_CTRL              = 0x00
REG_ALICE_PHASE_STAGED = 0x04
REG_BOB_PHASE_STAGED  = 0x08
REG_STATUS            = 0x0C
REG_PHASE_APPLY       = 0x10
REG_ALICE_PHASE_ACTIVE = 0x14
REG_BOB_PHASE_ACTIVE  = 0x18
REG_VERSION           = 0x1C

# Expected I/Q constants (from axis_psk_gen.sv)
POS_FULL = 30000
NEG_FULL = (-30000) & 0xFFFF
ZERO = 0

# phase_select → expected I/Q in sample slot 0
PHASE_IQ = {
    0b00: (POS_FULL, ZERO),
    0b01: (ZERO, POS_FULL),
    0b10: (NEG_FULL, ZERO),
    0b11: (ZERO, NEG_FULL),
}


def extract_iq(tdata_val, sample_idx=0):
    """Extract I and Q from a 64-bit tdata word."""
    val = int(tdata_val)
    sample = (val >> (sample_idx * 32)) & 0xFFFFFFFF
    i_val = (sample >> 16) & 0xFFFF
    q_val = sample & 0xFFFF
    return i_val, q_val


async def axi_write(dut, addr, data):
    """Simple AXI4-Lite write transaction."""
    await FallingEdge(dut.s_axi_aclk)
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awvalid.value = 1
    dut.s_axi_wdata.value = data
    dut.s_axi_wstrb.value = 0xF
    dut.s_axi_wvalid.value = 1
    dut.s_axi_bready.value = 1
    # Wait for both AWREADY and WREADY
    for _ in range(20):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_awready.value and dut.s_axi_wready.value:
            break
    await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wvalid.value = 0
    # Wait for BVALID
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_bvalid.value:
            break
    dut.s_axi_bready.value = 0
    await RisingEdge(dut.s_axi_aclk)


async def axi_read(dut, addr):
    """Simple AXI4-Lite read transaction. Returns read data."""
    await FallingEdge(dut.s_axi_aclk)
    dut.s_axi_araddr.value = addr
    dut.s_axi_arvalid.value = 1
    dut.s_axi_rready.value = 1
    # Wait for ARREADY
    for _ in range(20):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_arready.value:
            break
    dut.s_axi_arvalid.value = 0
    # Wait for RVALID
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_rvalid.value:
            break
    rdata = int(dut.s_axi_rdata.value)
    dut.s_axi_rready.value = 0
    await RisingEdge(dut.s_axi_aclk)
    return rdata


async def init_dut(dut):
    """Start clocks, reset, and initialize AXI signals."""
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, units="ns").start())   # 100 MHz PS clock
    cocotb.start_soon(Clock(dut.rfdc_aclk, 3, units="ns").start())     # ~333 MHz RFDC clock

    # Init AXI-Lite signals
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awprot.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arprot.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0

    # Init board I/O
    dut.sw.value = 0  # SW3=0 → switch mode, all switches off

    # DAC ready
    dut.m_axis_alice_tready.value = 1
    dut.m_axis_bob_tready.value = 1

    # Reset both clock domains
    dut.s_axi_aresetn.value = 0
    dut.rfdc_aresetn.value = 0
    await ClockCycles(dut.s_axi_aclk, 5)
    dut.s_axi_aresetn.value = 1
    dut.rfdc_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 5)


@cocotb.test()
async def test_switch_mode(dut):
    """Verify DIP switches control phase output in switch mode (SW3=0)."""
    await init_dut(dut)

    # Enable via registers: global_en + alice_en + bob_en
    await axi_write(dut, REG_CTRL, 0x07)

    # Wait for CDC + PSK gen to settle
    await ClockCycles(dut.rfdc_aclk, 10)

    # Test all Alice switch combinations
    # SW0=basis, SW1=bit
    test_cases = [
        # (sw_val, expected_alice_phase, expected_bob_phase)
        (0b0000, 0b00, 0b00),  # Z-basis bit 0, Bob Z → phase 0, phase 0
        (0b0010, 0b10, 0b00),  # Z-basis bit 1, Bob Z → phase pi, phase 0
        (0b0001, 0b01, 0b00),  # X-basis bit 0, Bob Z → phase pi/2, phase 0
        (0b0011, 0b11, 0b00),  # X-basis bit 1, Bob Z → phase 3pi/2, phase 0
        (0b0100, 0b00, 0b01),  # Z-basis bit 0, Bob X → phase 0, phase pi/2
        (0b0101, 0b01, 0b01),  # X-basis bit 0, Bob X → phase pi/2, phase pi/2
    ]

    for sw_val, exp_alice, exp_bob in test_cases:
        dut.sw.value = sw_val  # SW3=0 → switch mode
        # Wait for 2-FF sync + PSK gen register
        await ClockCycles(dut.rfdc_aclk, 10)
        await FallingEdge(dut.rfdc_aclk)
        await ReadOnly()

        exp_ai, exp_aq = PHASE_IQ[exp_alice]
        act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
        assert act_ai == exp_ai and act_aq == exp_aq, \
            f"sw={sw_val:#06b}: Alice I/Q expected ({exp_ai:#06x},{exp_aq:#06x}), got ({act_ai:#06x},{act_aq:#06x})"

        exp_bi, exp_bq = PHASE_IQ[exp_bob]
        act_bi, act_bq = extract_iq(dut.m_axis_bob_tdata.value, 0)
        assert act_bi == exp_bi and act_bq == exp_bq, \
            f"sw={sw_val:#06b}: Bob I/Q expected ({exp_bi:#06x},{exp_bq:#06x}), got ({act_bi:#06x},{act_bq:#06x})"

        # Check LEDs match active phases
        led_val = int(dut.led.value)
        expected_led = (exp_bob << 2) | exp_alice
        assert led_val == expected_led, \
            f"sw={sw_val:#06b}: LED expected {expected_led:#06b}, got {led_val:#06b}"

        # Return to writable phase before next iteration
        await RisingEdge(dut.rfdc_aclk)

    dut._log.info("Switch mode test passed for all combinations")


@cocotb.test()
async def test_register_mode_armed_trigger(dut):
    """Verify armed trigger latches both phases atomically in register mode."""
    await init_dut(dut)

    # Enable outputs
    await axi_write(dut, REG_CTRL, 0x07)

    # Switch to register mode: SW3=1
    dut.sw.value = 0b1000
    await ClockCycles(dut.rfdc_aclk, 10)

    # Stage Alice=pi (2'b10), Bob=pi/2 (2'b01)
    await axi_write(dut, REG_ALICE_PHASE_STAGED, 0x02)
    await axi_write(dut, REG_BOB_PHASE_STAGED, 0x01)

    # Phases should NOT have changed yet (still 0 from reset)
    await ClockCycles(dut.rfdc_aclk, 10)
    await FallingEdge(dut.rfdc_aclk)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b00]  # should still be phase 0
    assert act_ai == exp_ai and act_aq == exp_aq, \
        "Staged phase should NOT take effect before PHASE_APPLY"

    # Apply!
    await axi_write(dut, REG_PHASE_APPLY, 0x01)

    # Wait for CDC propagation
    await ClockCycles(dut.rfdc_aclk, 10)
    await FallingEdge(dut.rfdc_aclk)
    await ReadOnly()

    # Alice should now be phase pi
    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b10]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        f"Alice after apply: expected ({exp_ai:#06x},{exp_aq:#06x}), got ({act_ai:#06x},{act_aq:#06x})"

    # Bob should now be phase pi/2
    act_bi, act_bq = extract_iq(dut.m_axis_bob_tdata.value, 0)
    exp_bi, exp_bq = PHASE_IQ[0b01]
    assert act_bi == exp_bi and act_bq == exp_bq, \
        f"Bob after apply: expected ({exp_bi:#06x},{exp_bq:#06x}), got ({act_bi:#06x},{act_bq:#06x})"

    dut._log.info("Armed trigger atomic phase update OK")


@cocotb.test()
async def test_version_register(dut):
    """Verify VERSION register reads back correctly."""
    await init_dut(dut)

    version = await axi_read(dut, REG_VERSION)
    assert version == 0x2026_0425, f"VERSION expected 0x20260425, got {version:#010x}"
    dut._log.info(f"VERSION register OK: {version:#010x}")


@cocotb.test()
async def test_mode_mux(dut):
    """Verify SW3 toggles between switch and register control."""
    await init_dut(dut)

    # Enable outputs
    await axi_write(dut, REG_CTRL, 0x07)

    # Set up register mode with a known phase
    await axi_write(dut, REG_ALICE_PHASE_STAGED, 0x03)  # 3pi/2
    await axi_write(dut, REG_BOB_PHASE_STAGED, 0x02)    # pi
    await axi_write(dut, REG_PHASE_APPLY, 0x01)
    await ClockCycles(dut.rfdc_aclk, 10)

    # In switch mode (SW3=0), switches should override
    dut.sw.value = 0b0001  # SW3=0, Alice X-basis bit 0 → phase pi/2
    await ClockCycles(dut.rfdc_aclk, 10)
    await FallingEdge(dut.rfdc_aclk)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b01]  # pi/2 from switch
    assert act_ai == exp_ai and act_aq == exp_aq, \
        "Switch mode should override register values"

    # Return to writable phase
    await RisingEdge(dut.rfdc_aclk)

    # Flip to register mode (SW3=1)
    dut.sw.value = 0b1001  # SW3=1, other switches don't matter
    await ClockCycles(dut.rfdc_aclk, 10)
    await FallingEdge(dut.rfdc_aclk)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b11]  # 3pi/2 from register
    assert act_ai == exp_ai and act_aq == exp_aq, \
        f"Register mode: Alice expected 3pi/2, got I={act_ai:#06x} Q={act_aq:#06x}"

    dut._log.info("Mode mux switch/register toggle OK")


def qkd_top_runner():
    sim = os.getenv("SIM", "icarus")
    sources = [
        proj_path / "hdl" / "axis_psk_gen.sv",
        proj_path / "hdl" / "axi_lite_regs.sv",
        proj_path / "hdl" / "qkd_top_wrapper.sv",
    ]
    build_test_args = ["-Wall"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="qkd_top_wrapper",
        always=True,
        build_args=build_test_args,
        parameters={
            "SAMPLES_PER_BEAT": 2,
            "SAMPLE_WIDTH": 16,
        },
        timescale=('1ns', '1ps'),
        waves=True
    )
    runner.test(
        hdl_toplevel="qkd_top_wrapper",
        test_module=test_file,
        waves=True
    )


if __name__ == "__main__":
    qkd_top_runner()
