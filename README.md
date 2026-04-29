# qkd_bb84_phase

FPGA-based Quantum Key Distribution using the BB84 protocol with phase encoding. Targets the Xilinx RFSoC 4x2 board. Generates PSK (Phase-Shift Keying) I/Q baseband signals for two parties (Alice and Bob), driving the RF-DAC's internal NCO/mixer to produce phase-modulated RF output.

## Repository Structure

```
fpga/
  hdl/
    axis_psk_gen.sv         PSK I/Q baseband generator
    axi_lite_regs.sv        AXI4-Lite register file
    qkd_top_wrapper.sv      Top-level system integrator
    qkd_top_wrapper_bd.v    Verilog wrapper for Vivado block design
  sim/
    test_psk_gen.py          Cocotb tests for axis_psk_gen
    test_qkd_top.py          Cocotb tests for qkd_top_wrapper
    axis_tb_utils.py         Shared AXI4-Stream test utilities
  vivado/                    Vivado project files
  xdc/
    base.xdc                 Pin constraints (LEDs, switches)
```

## Running Simulations

Requires cocotb (<2.0) and Icarus Verilog.

```bash
source .venv/bin/activate
python3 fpga/sim/test_psk_gen.py
python3 fpga/sim/test_qkd_top.py
```

---

## HDL Modules

### axis_psk_gen.sv

Stateless PSK I/Q baseband generator. Takes a 2-bit phase select input and outputs a constant I/Q pair on an AXI4-Stream interface. The I/Q values are replicated across all sample slots in each beat.

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| SAMPLES_PER_BEAT | 4 | Samples packed per AXI-Stream beat |
| SAMPLE_WIDTH | 16 | Bit width of each I or Q sample |

**Ports:**

| Port | Dir | Width | Description |
|---|---|---|---|
| aclk | in | 1 | Clock |
| aresetn | in | 1 | Active-low reset |
| enable | in | 1 | Gates m_axis_tvalid |
| phase_select | in | 2 | Selects one of four phase states |
| m_axis_tdata | out | SAMPLES_PER_BEAT * SAMPLE_WIDTH * 2 | I/Q data |
| m_axis_tvalid | out | 1 | Valid when enabled |
| m_axis_tready | in | 1 | Backpressure from downstream |

**Phase-to-IQ Mapping:**

| phase_select | Phase | I | Q |
|---|---|---|---|
| 2'b00 | 0 | +30000 (0x7530) | 0 (0x0000) |
| 2'b01 | pi/2 | 0 (0x0000) | +30000 (0x7530) |
| 2'b10 | pi | -30000 (0x8AD0) | 0 (0x0000) |
| 2'b11 | 3pi/2 | 0 (0x0000) | -30000 (0x8AD0) |

Amplitude of 30000 is ~91.5% of 16-bit full-scale, leaving headroom for the DUC mixer.

**Data Packing (SAMPLES_PER_BEAT=2, 64-bit tdata):**

```
Bits [31:16] = I sample 0    Bits [15:0]  = Q sample 0
Bits [63:48] = I sample 1    Bits [47:32] = Q sample 1
```

All sample slots contain identical I/Q values.

---

### axi_lite_regs.sv

AXI4-Lite register file for PS control and status. Implements standard AXI4-Lite read/write state machines.

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| C_S_AXI_DATA_WIDTH | 32 | AXI data width |
| C_S_AXI_ADDR_WIDTH | 5 | AXI address width (8 registers) |

**Register Map:**

| Address | Name | R/W | Bits | Description |
|---|---|---|---|---|
| 0x00 | CTRL | R/W | [2:0] | {bob_en, alice_en, global_en} |
| 0x04 | ALICE_PHASE_STAGED | R/W | [1:0] | Staged Alice phase (inactive until PHASE_APPLY) |
| 0x08 | BOB_PHASE_STAGED | R/W | [1:0] | Staged Bob phase (inactive until PHASE_APPLY) |
| 0x0C | STATUS | R | [2:0] | {sw_mode, bob_running, alice_running} |
| 0x10 | PHASE_APPLY | R/W | [0] | Write 1 to atomically latch staged phases. Auto-clears after 1 cycle. |
| 0x14 | ALICE_PHASE_ACTIVE | R | [1:0] | Currently active Alice phase (readback from RFDC domain) |
| 0x18 | BOB_PHASE_ACTIVE | R | [1:0] | Currently active Bob phase (readback from RFDC domain) |
| 0x1C | VERSION | R | [31:0] | Fixed value 0x2026_0425 |

**Control Ports (directly wired to/from qkd_top_wrapper):**

| Port | Dir | Width | Description |
|---|---|---|---|
| ctrl | out | 3 | CTRL register value |
| alice_phase_staged | out | 2 | ALICE_PHASE_STAGED register value |
| bob_phase_staged | out | 2 | BOB_PHASE_STAGED register value |
| phase_apply | out | 1 | PHASE_APPLY bit (auto-clears) |
| status | in | 3 | STATUS register input |
| alice_phase_active | in | 2 | Active Alice phase readback |
| bob_phase_active | in | 2 | Active Bob phase readback |

---

### qkd_top_wrapper.sv

Top-level system integrator. Instantiates the register file and two PSK generators (Alice, Bob). Handles clock domain crossing, switch decoding, mode muxing, and the armed phase trigger.

**Parameters:**

| Parameter | Default (sv) | Default (bd.v) | Description |
|---|---|---|---|
| C_S_AXI_DATA_WIDTH | 32 | 32 | AXI data width |
| C_S_AXI_ADDR_WIDTH | 5 | 5 | AXI address width |
| SAMPLES_PER_BEAT | 4 | 2 | Samples per beat (hardware uses 2) |
| SAMPLE_WIDTH | 16 | 16 | Sample bit width |

Note: The .sv default of 4 is overridden to 2 by the .bd.v wrapper and by the cocotb test runners. The RFDC on the RFSoC 4x2 in I/Q->Real mixer mode expects 2 complex samples per beat (64-bit tdata).

**Clock Domains:**

| Clock | Frequency | Domain |
|---|---|---|
| s_axi_aclk | ~100 MHz | PS AXI register access |
| rfdc_aclk | ~307 MHz | PSK generators, DAC AXI-Stream outputs |

**Clock Domain Crossing (2-FF synchronizers):**

s_axi_aclk -> rfdc_aclk:
- Switch inputs (sw)
- Staged phase values (alice_phase_staged, bob_phase_staged)
- Phase apply pulse (with rising-edge detection: `phase_apply_sync & ~phase_apply_prev`)
- Control register (ctrl)

rfdc_aclk -> s_axi_aclk:
- Active phase readback (alice_phase_active, bob_phase_active)
- Switch mode indicator (sw_mode)

All crossed signals are quasi-static or low-rate (~320 Hz protocol), so 2-FF synchronization is sufficient.

**Switch Decode (on rfdc_aclk, after sync):**

SW3 selects the operating mode: 0 = switch mode, 1 = register mode.

Alice phase from switches (SW0=basis, SW1=bit):

| SW0 (basis) | SW1 (bit) | Phase | Encoding |
|---|---|---|---|
| 0 (Z) | 0 | 0 | 2'b00 |
| 0 (Z) | 1 | pi | 2'b10 |
| 1 (X) | 0 | pi/2 | 2'b01 |
| 1 (X) | 1 | 3pi/2 | 2'b11 |

Logic: `sw_alice_phase = {sw_sync[1], sw_sync[0]}`

Bob phase from switches (SW2=basis, no bit selection):

| SW2 (basis) | Phase | Encoding |
|---|---|---|
| 0 (Z) | 0 | 2'b00 |
| 1 (X) | pi/2 | 2'b01 |

Logic: `sw_bob_phase = {1'b0, sw_sync[2]}`

**Armed Trigger:**

Phase values written to ALICE_PHASE_STAGED and BOB_PHASE_STAGED do not take effect until PHASE_APPLY is written. On the rising edge of the synchronized apply pulse, both phases are atomically latched into `reg_alice_active` and `reg_bob_active`. This prevents partial or inconsistent phase updates.

**Mode Mux:**

```
alice_phase_active = sw_mode ? sw_alice_phase : reg_alice_active
bob_phase_active   = sw_mode ? sw_bob_phase   : reg_bob_active
```

**Enable Logic:**

```
alice_running = ctrl_sync[0] (global_en) & ctrl_sync[1] (alice_en)
bob_running   = ctrl_sync[0] (global_en) & ctrl_sync[2] (bob_en)
```

**LED Output:**

```
led[3:0] = {bob_phase_active[1:0], alice_phase_active[1:0]}
```

**Submodule Instantiations:**

- `u_regs` (axi_lite_regs) — on s_axi_aclk
- `u_alice` (axis_psk_gen) — on rfdc_aclk, enable=alice_running, phase_select=alice_phase_active
- `u_bob` (axis_psk_gen) — on rfdc_aclk, enable=bob_running, phase_select=bob_phase_active

---

### qkd_top_wrapper_bd.v

Verilog passthrough wrapper for Vivado block design integration. Vivado's "Add Module" requires .v files. All ports are directly wired to the SystemVerilog qkd_top_wrapper instance (`u_core`). No logic is added.

---

## Cocotb Tests

### Shared Utilities (axis_tb_utils.py)

**AXISMonitor** — Monitors an AXI4-Stream bus. On each rising edge, checks for tvalid & tready handshake and records tdata via a callback. Tracks transaction count.

**AXISDriver** — Drives AXI4-Stream as Master or Slave. Supports write_single, write_burst, pause (Master) and read_single, read_burst, ready_high, pause (Slave) transactions.

**reset(clk, rst, cycles_held, polarity)** — Asserts reset at the given polarity for N cycles, then releases.

---

### test_psk_gen.py

Tests for `axis_psk_gen` module. Clock: 3ns period (333 MHz). Parameters: SAMPLES_PER_BEAT=2, SAMPLE_WIDTH=16.

#### test_phase_states

Verifies all 4 phase states produce correct I/Q output.

1. Reset, enable the generator, set tready=1
2. For each phase_select value (0b00, 0b01, 0b10, 0b11):
   - Set phase_select, wait 3 cycles for registered output
   - Sample tdata at both sample slots (0 and 1)

**Expected outputs:**

| phase_select | I (slot 0) | Q (slot 0) | I (slot 1) | Q (slot 1) | tvalid |
|---|---|---|---|---|---|
| 0b00 | 0x7530 | 0x0000 | 0x7530 | 0x0000 | 1 |
| 0b01 | 0x0000 | 0x7530 | 0x0000 | 0x7530 | 1 |
| 0b10 | 0x8AD0 | 0x0000 | 0x8AD0 | 0x0000 | 1 |
| 0b11 | 0x0000 | 0x8AD0 | 0x0000 | 0x8AD0 | 1 |

#### test_phase_switch_latency

Verifies phase switches propagate within 2 clock cycles.

1. Reset, enable, stabilize at phase 0b00
2. Switch phase_select to 0b10 (pi)
3. Wait exactly 2 clock cycles, sample output

**Expected output after 2 cycles:**

| I | Q |
|---|---|
| 0x8AD0 (-30000) | 0x0000 |

#### test_enable_gating

Verifies tvalid is low when disabled and high when enabled.

1. Reset with enable=0 -> assert tvalid == 0
2. Set enable=1 -> assert tvalid == 1
3. Set enable=0 -> assert tvalid == 0

#### test_backpressure

Verifies the module handles tready deassertion gracefully.

1. Reset, enable with phase=pi/2, hold tready=0
2. After 10 cycles: assert no transactions received, assert tvalid == 1 (stays valid under backpressure)
3. Release tready=1, wait 10 cycles: assert transactions received > 0

---

### test_qkd_top.py

Tests for `qkd_top_wrapper` module. PS clock: 10ns (100 MHz). RFDC clock: 3ns (333 MHz). Parameters: SAMPLES_PER_BEAT=2, SAMPLE_WIDTH=16.

AXI helper functions `axi_write(dut, addr, data)` and `axi_read(dut, addr)` implement full AXI4-Lite handshake with timeouts.

#### test_switch_mode

Verifies DIP switches control phase output in switch mode (SW3=0).

1. Enable all channels via CTRL register (write 0x07)
2. For 6 switch combinations, set sw value, wait 10 RFDC cycles for CDC, sample outputs

**Expected outputs:**

| sw[3:0] | Alice Phase | Alice I | Alice Q | Bob Phase | Bob I | Bob Q | LED[3:0] |
|---|---|---|---|---|---|---|---|
| 0b0000 | 0 | 0x7530 | 0x0000 | 0 | 0x7530 | 0x0000 | 0b0000 |
| 0b0010 | pi | 0x8AD0 | 0x0000 | 0 | 0x7530 | 0x0000 | 0b0010 |
| 0b0001 | pi/2 | 0x0000 | 0x7530 | 0 | 0x7530 | 0x0000 | 0b0001 |
| 0b0011 | 3pi/2 | 0x0000 | 0x8AD0 | 0 | 0x7530 | 0x0000 | 0b0011 |
| 0b0100 | 0 | 0x7530 | 0x0000 | pi/2 | 0x0000 | 0x7530 | 0b0100 |
| 0b0101 | pi/2 | 0x0000 | 0x7530 | pi/2 | 0x0000 | 0x7530 | 0b0101 |

#### test_register_mode_armed_trigger

Verifies staged phases don't take effect until PHASE_APPLY, then both latch atomically.

1. Enable all channels, set SW3=1 (register mode)
2. Stage Alice=0x02 (pi), Bob=0x01 (pi/2) — do NOT apply
3. Sample Alice output -> **should still be phase 0** (staged values inactive)

| Before apply | Alice I | Alice Q |
|---|---|---|
| Expected | 0x7530 | 0x0000 |

4. Write PHASE_APPLY=1, wait 10 RFDC cycles
5. Sample both outputs -> **both should now reflect staged values**

| After apply | I | Q |
|---|---|---|
| Alice (pi) | 0x8AD0 | 0x0000 |
| Bob (pi/2) | 0x0000 | 0x7530 |

#### test_version_register

Reads VERSION register at 0x1C.

**Expected output:** 0x2026_0425

#### test_mode_mux

Verifies SW3 toggles between switch and register control.

1. Enable all channels, stage Alice=3pi/2, Bob=pi via registers, apply
2. Set sw=0b0001 (SW3=0, switch mode, Alice X-basis bit 0)
   - Alice output should follow switches: **phase pi/2**

| Switch mode | Alice I | Alice Q |
|---|---|---|
| Expected | 0x0000 | 0x7530 |

3. Set sw=0b1001 (SW3=1, register mode)
   - Alice output should follow registers: **phase 3pi/2**

| Register mode | Alice I | Alice Q |
|---|---|---|
| Expected | 0x0000 | 0x8AD0 |
