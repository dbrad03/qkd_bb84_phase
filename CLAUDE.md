# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FPGA-based Quantum Key Distribution (QKD) implementation using the BB84 protocol with phase encoding. Targets a Xilinx Zynq UltraScale+ RFSoC 4x2 board. The design generates PSK (Phase-Shift Keying) I/Q baseband signals for two parties (Alice and Bob) with three control modes: physical DIP switches, AXI-Lite registers, or automatic phase cycling with button-controlled frequency.

## Running Simulations

Tests use cocotb with Icarus Verilog (default). Each testbench is a standalone Python script:

```bash
# PSK generator unit tests (4 tests)
python3 fpga/sim/test_psk_gen.py

# Button debounce unit tests (3 tests)
python3 fpga/sim/test_btn_debounce.py

# Top-level wrapper integration tests (11 tests: dual-channel, auto-cycle, muting)
python3 fpga/sim/test_qkd_top.py

# Use a different simulator
SIM=verilator python3 fpga/sim/test_psk_gen.py
```

Dependencies: `cocotb`, `cocotb-bus`, and a supported HDL simulator (Icarus Verilog by default). Virtual env at `/home/dylan/62410/.venv/`.

## Architecture

### HDL Modules (`fpga/hdl/`)

**`axis_psk_gen`** — I/Q generator. Takes a 2-bit `phase_select` and outputs a fixed I/Q pair on AXI4-Stream. When `enable=0`, outputs zeros with `tvalid=1` (clean RF muting). Four phases: 0/90/180/270 degrees mapped to +/-30000 (16-bit).

**`axi_lite_regs`** — Register file. CTRL (global/alice/bob enable + auto_cycle_en), ALICE/BOB_PHASE_STAGED, PHASE_APPLY (atomic latch), STATUS, ACTIVE_PHASES (packed readback), AUTO_FREQ_HZ, VERSION (0x2026_0508).

**`btn_debounce`** — Per-button 2-FF sync + counter debounce with single-cycle pulse output. Parameterized CLK_FREQ_HZ and DEBOUNCE_MS for sim override.

**`auto_phase_cycler`** — Dual-clock module. Button logic + sequential divider in s_axi_aclk domain, phase counter in rfdc domain. Buttons control unit (Hz/KHz/MHz), increment (1/5/10), up/down. Supports 0/pi toggle mode with CDC-safe phase snap.

**`qkd_top_wrapper`** — System integrator. Two `axis_psk_gen` (Alice/Bob), `axi_lite_regs`, `btn_debounce`, `auto_phase_cycler`, mode mux, CDC. Three clock domains: `s_axi_aclk` (~100 MHz), `rfdc_aclk_0` (~307.2 MHz Alice), `rfdc_aclk_2` (~307.2 MHz Bob).

**`qkd_top_wrapper_bd.v`** — Thin Verilog wrapper with Vivado X_INTERFACE attributes for block design.

### Operating Modes

- **SW3=0**: Switch mode (SW0/SW1 set Alice phase, SW2 sets Bob phase)
- **SW3=1, SW2=0**: Register mode (AXI-Lite staged/apply for both channels)
- **SW3=1, SW2=1**: Auto-cycle (Alice cycles phases, buttons control rate, SW1 selects full cycle vs 0/pi toggle)

### Simulation (`fpga/sim/`)

**`axis_tb_utils.py`** — Shared cocotb helpers. Shared with the FPGA-GGX-Sampler project.

Test files use `cocotb.runner.get_runner()` — no Makefile needed.

### Vivado Project

`fpga/vivado/qkd_phase_bb84/qkd_phase_bb84.xpr` — Vivado 2025.1. Constraints in `fpga/xdc/base.xdc`.

## Key Design Patterns

- **Armed trigger for atomic updates**: PHASE_APPLY latches both Alice and Bob phases simultaneously.
- **Clock domain crossing**: 2-FF synchronizers for all quasi-static signals between PS and RF-DAC domains.
- **Clean RF muting**: Disabled channels output I=0, Q=0 with tvalid=1 (DAC sees zeros, carrier off).
- **Sequential divider**: 32-cycle shift-subtract computes period_ticks from freq_hz on button press.
- **Toggle mode with phase snap**: When toggle_mode asserts mid-cycle, phase snaps to nearest valid value (0 or pi).

## Domain Context

BB84 phase encoding uses four phase states (0, pi/2, pi, 3pi/2) across two conjugate bases. Alice's AOM driven at ~100 MHz, Bob's AOM at ~90 MHz, producing a 10 MHz heterodyne beat note detectable by APD210. NCO frequencies are set from Python, not hard-coded in RTL.
