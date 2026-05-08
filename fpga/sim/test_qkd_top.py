#!/usr/bin/env python3
"""
Cocotb testbench for qkd_top_wrapper (dual-channel + auto-cycle).

Tests:
  1. Switch mode: DIP switches control Alice phase output and LEDs
  2. Register mode: AXI-Lite staged phases, PHASE_APPLY latches both
  3. Version register readback
  4. Mode mux: SW3 toggles between switch and register control
  5. Auto-cycle basic: Alice phases cycle 0→1→2→3→0
  6. Auto-cycle frequency change via buttons
  7. Auto-cycle unit/increment cycling via buttons
  8. Auto-cycle frequency clamp at lower bound
  9. Auto-cycle 0/pi toggle mode
  10. Bob register mode: stage and apply Bob phase independently
  11. On/off muting: disabled channel outputs zeros with tvalid=1
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
REG_CTRL               = 0x00
REG_ALICE_PHASE_STAGED = 0x04
REG_BOB_PHASE_STAGED   = 0x08
REG_STATUS             = 0x0C
REG_PHASE_APPLY        = 0x10
REG_ACTIVE_PHASES      = 0x14   # [1:0] alice, [3:2] bob
REG_AUTO_FREQ_HZ       = 0x18
REG_VERSION            = 0x1C

# CTRL bits: [0] global_en, [1] alice_en, [2] bob_en, [3] auto_cycle_en
CTRL_GLOBAL     = 0x01
CTRL_ALICE      = 0x02
CTRL_BOB        = 0x04
CTRL_AUTO_CYCLE = 0x08

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

# Muted output (enable=0)
MUTED_IQ = (ZERO, ZERO)

# Sim parameters (set in runner)
SIM_RFDC_CLK_FREQ = 500


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
    for _ in range(20):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_awready.value and dut.s_axi_wready.value:
            break
    await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wvalid.value = 0
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
    for _ in range(20):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_arready.value:
            break
    dut.s_axi_arvalid.value = 0
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
        if dut.s_axi_rvalid.value:
            break
    rdata = int(dut.s_axi_rdata.value)
    dut.s_axi_rready.value = 0
    await RisingEdge(dut.s_axi_aclk)
    return rdata


async def press_btn(dut, btn_idx, hold_cycles=5):
    """Simulate a button press (assert, hold, release)."""
    val = int(dut.btn.value)
    dut.btn.value = val | (1 << btn_idx)
    await ClockCycles(dut.s_axi_aclk, hold_cycles)
    dut.btn.value = val & ~(1 << btn_idx)
    await ClockCycles(dut.s_axi_aclk, 10)


async def init_dut(dut):
    """Start clocks, reset, and initialize AXI signals."""
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, units="ns").start())    # 100 MHz
    cocotb.start_soon(Clock(dut.rfdc_aclk_0, 3, units="ns").start())    # ~333 MHz Alice
    cocotb.start_soon(Clock(dut.rfdc_aclk_2, 3, units="ns").start())    # ~333 MHz Bob

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
    dut.sw.value = 0
    dut.btn.value = 0

    # DAC ready
    dut.m_axis_alice_tready.value = 1
    dut.m_axis_bob_tready.value = 1

    # Reset all clock domains
    dut.s_axi_aresetn.value = 0
    dut.rfdc_aresetn_0.value = 0
    dut.rfdc_aresetn_2.value = 0
    await ClockCycles(dut.s_axi_aclk, 5)
    dut.s_axi_aresetn.value = 1
    dut.rfdc_aresetn_0.value = 1
    dut.rfdc_aresetn_2.value = 1
    await ClockCycles(dut.s_axi_aclk, 5)


@cocotb.test()
async def test_switch_mode(dut):
    """Verify DIP switches control Alice phase output in switch mode (SW3=0)."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE)
    await ClockCycles(dut.rfdc_aclk_0, 10)

    test_cases = [
        (0b0000, 0b00),
        (0b0010, 0b10),
        (0b0001, 0b01),
        (0b0011, 0b11),
    ]

    for sw_val, exp_alice in test_cases:
        dut.sw.value = sw_val
        await ClockCycles(dut.rfdc_aclk_0, 10)
        await FallingEdge(dut.rfdc_aclk_0)
        await ReadOnly()

        exp_ai, exp_aq = PHASE_IQ[exp_alice]
        act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
        assert act_ai == exp_ai and act_aq == exp_aq, \
            f"sw={sw_val:#06b}: Alice expected ({exp_ai:#06x},{exp_aq:#06x}), got ({act_ai:#06x},{act_aq:#06x})"

        led_val = int(dut.led.value)
        expected_led = (0 << 3) | (1 << 2) | exp_alice
        assert led_val == expected_led, \
            f"sw={sw_val:#06b}: LED expected {expected_led:#06b}, got {led_val:#06b}"

        await RisingEdge(dut.rfdc_aclk_0)

    dut._log.info("Switch mode test passed for all combinations")


@cocotb.test()
async def test_register_mode_armed_trigger(dut):
    """Verify armed trigger latches both Alice and Bob phases atomically."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_BOB)

    # Register mode: SW3=1
    dut.sw.value = 0b1000
    await ClockCycles(dut.rfdc_aclk_0, 10)

    # Stage Alice=pi, Bob=pi/2
    await axi_write(dut, REG_ALICE_PHASE_STAGED, 0x02)
    await axi_write(dut, REG_BOB_PHASE_STAGED, 0x01)

    # Not yet applied
    await ClockCycles(dut.rfdc_aclk_0, 10)
    await FallingEdge(dut.rfdc_aclk_0)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b00]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        "Alice staged phase should NOT take effect before PHASE_APPLY"

    # Apply
    await axi_write(dut, REG_PHASE_APPLY, 0x01)
    await ClockCycles(dut.rfdc_aclk_0, 10)
    await FallingEdge(dut.rfdc_aclk_0)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b10]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        f"Alice after apply: expected pi, got ({act_ai:#06x},{act_aq:#06x})"

    await RisingEdge(dut.rfdc_aclk_0)

    # Check Bob too
    await ClockCycles(dut.rfdc_aclk_2, 10)
    await FallingEdge(dut.rfdc_aclk_2)
    await ReadOnly()

    act_bi, act_bq = extract_iq(dut.m_axis_bob_tdata.value, 0)
    exp_bi, exp_bq = PHASE_IQ[0b01]
    assert act_bi == exp_bi and act_bq == exp_bq, \
        f"Bob after apply: expected pi/2, got ({act_bi:#06x},{act_bq:#06x})"

    dut._log.info("Armed trigger atomic phase update OK (both channels)")


@cocotb.test()
async def test_version_register(dut):
    """Verify VERSION register reads back correctly."""
    await init_dut(dut)

    version = await axi_read(dut, REG_VERSION)
    assert version == 0x2026_0508, f"VERSION expected 0x20260508, got {version:#010x}"
    dut._log.info(f"VERSION register OK: {version:#010x}")


@cocotb.test()
async def test_mode_mux(dut):
    """Verify SW3 toggles between switch and register control."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE)

    await axi_write(dut, REG_ALICE_PHASE_STAGED, 0x03)
    await axi_write(dut, REG_PHASE_APPLY, 0x01)
    await ClockCycles(dut.rfdc_aclk_0, 10)

    # Switch mode (SW3=0)
    dut.sw.value = 0b0001
    await ClockCycles(dut.rfdc_aclk_0, 10)
    await FallingEdge(dut.rfdc_aclk_0)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b01]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        "Switch mode should override register values"

    await RisingEdge(dut.rfdc_aclk_0)

    # Register mode (SW3=1, SW2=0)
    dut.sw.value = 0b1001
    await ClockCycles(dut.rfdc_aclk_0, 10)
    await FallingEdge(dut.rfdc_aclk_0)
    await ReadOnly()

    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b11]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        f"Register mode: Alice expected 3pi/2, got I={act_ai:#06x} Q={act_aq:#06x}"

    dut._log.info("Mode mux switch/register toggle OK")


@cocotb.test()
async def test_auto_cycle_basic(dut):
    """Verify auto-cycle mode cycles through all 4 phases."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_AUTO_CYCLE)
    dut.sw.value = 0b1100  # SW3=1, SW2=1

    await ClockCycles(dut.s_axi_aclk, 50)
    await ClockCycles(dut.rfdc_aclk_0, 10)

    phases_seen = []
    for _ in range(2500):
        await RisingEdge(dut.rfdc_aclk_0)
        await ReadOnly()
        ai, aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
        for phase, (ei, eq) in PHASE_IQ.items():
            if ai == ei and aq == eq:
                if not phases_seen or phases_seen[-1] != phase:
                    phases_seen.append(phase)
                break

    dut._log.info(f"Phases seen in sequence: {phases_seen}")

    found_cycle = False
    for i in range(len(phases_seen) - 3):
        seq = phases_seen[i:i+4]
        if seq == [(phases_seen[i] + j) % 4 for j in range(4)]:
            found_cycle = True
            break

    assert found_cycle, f"Expected sequential phase cycle, got: {phases_seen}"
    dut._log.info("Auto-cycle basic test passed")


@cocotb.test()
async def test_auto_cycle_freq_change(dut):
    """Verify button press changes auto-cycle frequency."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_AUTO_CYCLE)
    dut.sw.value = 0b1100

    await ClockCycles(dut.s_axi_aclk, 50)

    freq_before = await axi_read(dut, REG_AUTO_FREQ_HZ)
    dut._log.info(f"Before: freq={freq_before} Hz")
    assert freq_before == 1, f"Initial freq should be 1, got {freq_before}"

    await press_btn(dut, 1)
    await ClockCycles(dut.s_axi_aclk, 50)

    freq_after = await axi_read(dut, REG_AUTO_FREQ_HZ)
    dut._log.info(f"After btn[1]: freq={freq_after} Hz")

    assert freq_after == 2, f"Expected freq=2 after btn[1], got {freq_after}"
    dut._log.info("Auto-cycle frequency change test passed")


@cocotb.test()
async def test_auto_cycle_unit_incr(dut):
    """Verify btn[3] cycles unit and btn[2] cycles increment."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_AUTO_CYCLE)
    dut.sw.value = 0b1100

    await ClockCycles(dut.s_axi_aclk, 50)

    freq = await axi_read(dut, REG_AUTO_FREQ_HZ)
    assert freq == 1

    await press_btn(dut, 2)
    await ClockCycles(dut.s_axi_aclk, 5)
    await press_btn(dut, 1)
    await ClockCycles(dut.s_axi_aclk, 50)

    freq = await axi_read(dut, REG_AUTO_FREQ_HZ)
    assert freq == 6, f"Expected freq=6 (1+5), got {freq}"

    await press_btn(dut, 2)
    await ClockCycles(dut.s_axi_aclk, 5)
    await press_btn(dut, 1)
    await ClockCycles(dut.s_axi_aclk, 50)

    freq = await axi_read(dut, REG_AUTO_FREQ_HZ)
    assert freq == 16, f"Expected freq=16 (6+10), got {freq}"

    dut._log.info("Auto-cycle unit/increment test passed")


@cocotb.test()
async def test_auto_cycle_clamp(dut):
    """Verify frequency does not go below 1 Hz."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_AUTO_CYCLE)
    dut.sw.value = 0b1100

    await ClockCycles(dut.s_axi_aclk, 50)

    await press_btn(dut, 0)
    await ClockCycles(dut.s_axi_aclk, 50)

    freq = await axi_read(dut, REG_AUTO_FREQ_HZ)
    assert freq == 1, f"Expected freq clamped to 1, got {freq}"

    dut._log.info("Auto-cycle clamp test passed")


@cocotb.test()
async def test_auto_cycle_toggle_mode(dut):
    """Verify SW1=1 during auto-cycle gives 0/pi toggle instead of full cycle."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE | CTRL_AUTO_CYCLE)
    dut.sw.value = 0b1110  # SW3=1, SW2=1, SW1=1

    await ClockCycles(dut.s_axi_aclk, 50)
    await ClockCycles(dut.rfdc_aclk_0, 10)

    phases_seen = set()
    transitions = []
    prev_phase = None
    for _ in range(2500):
        await RisingEdge(dut.rfdc_aclk_0)
        await ReadOnly()
        ai, aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
        for phase, (ei, eq) in PHASE_IQ.items():
            if ai == ei and aq == eq:
                phases_seen.add(phase)
                if prev_phase is not None and phase != prev_phase:
                    transitions.append((prev_phase, phase))
                prev_phase = phase
                break

    dut._log.info(f"Toggle mode phases seen: {phases_seen}, transitions: {transitions[:6]}")

    assert phases_seen <= {0, 2}, f"Toggle mode should only produce 0 and 2, got {phases_seen}"
    assert 0 in phases_seen and 2 in phases_seen, f"Should see both 0 and 2, got {phases_seen}"

    for src, dst in transitions:
        assert (src, dst) in [(0, 2), (2, 0)], \
            f"Unexpected transition {src}→{dst} in toggle mode"

    dut._log.info("Auto-cycle toggle mode test passed")


@cocotb.test()
async def test_bob_register_mode(dut):
    """Verify Bob phase can be staged, applied, and read back."""
    await init_dut(dut)

    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_BOB)

    # Register mode: SW3=1
    dut.sw.value = 0b1000
    await ClockCycles(dut.rfdc_aclk_2, 10)

    # Stage Bob=pi
    await axi_write(dut, REG_BOB_PHASE_STAGED, 0x02)
    await axi_write(dut, REG_PHASE_APPLY, 0x01)

    await ClockCycles(dut.rfdc_aclk_2, 10)
    await FallingEdge(dut.rfdc_aclk_2)
    await ReadOnly()

    act_bi, act_bq = extract_iq(dut.m_axis_bob_tdata.value, 0)
    exp_bi, exp_bq = PHASE_IQ[0b10]
    assert act_bi == exp_bi and act_bq == exp_bq, \
        f"Bob expected pi, got ({act_bi:#06x},{act_bq:#06x})"

    # Read back active phases (packed: [1:0]=alice, [3:2]=bob)
    active = await axi_read(dut, REG_ACTIVE_PHASES)
    bob_active = (active >> 2) & 0x3
    assert bob_active == 0x02, f"Bob active readback expected 2, got {bob_active}"

    dut._log.info("Bob register mode test passed")


@cocotb.test()
async def test_on_off_muting(dut):
    """Verify disabled channel outputs I=0, Q=0 with tvalid=1."""
    await init_dut(dut)

    # Enable only Alice, Bob disabled
    await axi_write(dut, REG_CTRL, CTRL_GLOBAL | CTRL_ALICE)
    dut.sw.value = 0b1000  # register mode

    await ClockCycles(dut.rfdc_aclk_2, 10)
    await FallingEdge(dut.rfdc_aclk_2)
    await ReadOnly()

    # Bob should output zeros (muted)
    act_bi, act_bq = extract_iq(dut.m_axis_bob_tdata.value, 0)
    assert act_bi == ZERO and act_bq == ZERO, \
        f"Muted Bob should output (0,0), got ({act_bi:#06x},{act_bq:#06x})"

    # Bob tvalid should still be high
    assert int(dut.m_axis_bob_tvalid.value) == 1, \
        "Muted channel should have tvalid=1 (outputting zeros)"

    # Alice should be active (phase 0 from reset)
    await FallingEdge(dut.rfdc_aclk_0)
    await ReadOnly()
    act_ai, act_aq = extract_iq(dut.m_axis_alice_tdata.value, 0)
    exp_ai, exp_aq = PHASE_IQ[0b00]
    assert act_ai == exp_ai and act_aq == exp_aq, \
        "Alice should be active while Bob is muted"

    dut._log.info("On/off muting test passed")


def qkd_top_runner():
    sim = os.getenv("SIM", "icarus")
    sources = [
        proj_path / "hdl" / "axis_psk_gen.sv",
        proj_path / "hdl" / "axi_lite_regs.sv",
        proj_path / "hdl" / "btn_debounce.sv",
        proj_path / "hdl" / "auto_phase_cycler.sv",
        proj_path / "hdl" / "qkd_top_wrapper.sv",
    ]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="qkd_top_wrapper",
        always=True,
        build_args=["-Wall"],
        parameters={
            "SAMPLES_PER_BEAT": 2,
            "SAMPLE_WIDTH": 16,
            "BTN_CLK_FREQ_HZ": 100,
            "RFDC_CLK_FREQ_HZ": SIM_RFDC_CLK_FREQ,
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
