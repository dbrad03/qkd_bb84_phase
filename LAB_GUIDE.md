# Lab Guide: RFSoC-Driven AOD for Phase-Encoded BB84 QKD

## Overview

This system uses a RealDigital RFSoC 4x2 (Zynq UltraScale+ ZU48DR) to drive an acousto-optic device for a BB84 phase-encoded QKD demonstration. The RFSoC replaces the SRS SG384 signal generator, providing fast digitally-controlled PSK modulation at 150 MHz.

The FPGA synthesizes a 150 MHz I/Q baseband signal whose phase is switched between 0, pi/2, pi, and 3pi/2 (the four BB84 phase states). An on-chip NCO/mixer in the RF-DAC upconverts this to a 150 MHz RF carrier. The phase switching rate is programmable from 1 Hz up to ~76 MHz via on-board buttons.

## How Phase Modulation Shows Up as Light

The AOD sits in one arm of a fiber-based Mach-Zehnder interferometer. When the FPGA changes the phase of the 150 MHz RF drive, the acoustic wave in the AOD crystal shifts phase, and the diffracted laser beam inherits that phase shift.

The interferometer converts phase differences into intensity differences:

```
I_out = I_0 * cos²(Δφ / 2)
```

| FPGA Phase | Δφ between arms | Interference | Photodiode sees |
|------------|-----------------|--------------|-----------------|
| 0          | 0               | Constructive | Bright (high V) |
| pi/2       | pi/2            | Partial      | ~50% intensity  |
| pi         | pi              | Destructive  | Dark (low V)    |
| 3pi/2      | 3pi/2           | Partial      | ~50% intensity  |

In **0/pi toggle mode** (SW1=1), the photodiode voltage switches between bright and dark — a square wave at the switching frequency on your scope. As you increase the switching rate past the AOD's acoustic bandwidth, the square wave edges soften, the modulation depth shrinks, and eventually the output flatlines. That rolloff frequency is the AOD's usable bandwidth for phase modulation.

Because only one arm is modulated, the reference arm provides a stable phase baseline. This is exactly how phase-encoded QKD works: Alice's modulator encodes information as phase shifts; the interferometer at the output converts those to measurable intensity changes.

## AOD vs AOM

Same underlying physics: Bragg diffraction of light from an acoustic grating in a crystal (typically TeO2).

- **AOM** (Acousto-Optic Modulator): Optimized for on/off switching or single-frequency operation. Narrow bandwidth.
- **AOD** (Acousto-Optic Deflector): Optimized for beam steering across a range of RF frequencies. Wide bandwidth (your Brimrose unit covers 100-200 MHz).

Your Brimrose TED-150-100-785/2mm is an AOD, but you're using it as a fixed-frequency phase modulator at 150 MHz. This works fine — the AOD just has more bandwidth than you need for single-frequency operation.

## Hardware

### Equipment List

| Item | Model | Notes |
|------|-------|-------|
| FPGA board | RealDigital RFSoC 4x2 | 14-bit DAC at 4.9152 GS/s. SMA output. |
| AOD | Brimrose TED-150-100-785/2mm | 150 MHz center, ~100 MHz BW (100-200 MHz), 2 mm aperture. Designed for 785 nm; at 633 nm expect ~30-50% diffraction efficiency. |
| Power amp | Mini-Circuits ZHL-3A+ | 0.4-150 MHz, +30 dBm P1dB, ~25 dB gain. BNC in/out. Requires +24 V / >1 A. |
| Low-pass filter | Mini-Circuits SLP-200+ or BLP-200+ | ~200 MHz cutoff. Mandatory — see below. |
| Laser | HeNe 633 nm | Focus to <0.5 mm through AOD for fast switching. |
| Photodiode | Single photodiode + TIA | At one output port of the interferometer. |
| Attenuator | Fixed SMA pad | Between amp output and AOD input. |
| Power supply | Bench supply, +24 V / 2 A | For ZHL-3A+. Current-limit to 1.5 A. |
| Adapters | SMA-to-BNC | RFSoC outputs SMA; ZHL-3A+ uses BNC. |
| Fiber BS | 50/50 fiber beamsplitter | For recombining the two interferometer arms. |

### RF Signal Chain

```
RFSoC DAC0 (SMA) ──> LPF 200 MHz ──> SMA-to-BNC ──> ZHL-3A+ ──> attenuator ──> AOD
     ~0 dBm                                            ~+25 dBm
```

**Why the LPF is mandatory:** The RFSoC DAC outputs images at multiples of f_s (4.9 GHz) and clock spurs. The SG384 had internal filtering; the RFSoC does not. Without the LPF you waste amp power on out-of-band garbage and may dump spurs into the AOD's passband. An SLP-200+ is cheap.

**ZHL-3A+ at 150 MHz:** This amp is spec'd to 150 MHz — that's the -3 dB upper edge. Gain at 150 MHz is slightly reduced vs mid-band. Still adequate (~+23 to +25 dBm output). If gain rolloff is a problem, the AOD works across 100-200 MHz; you could shift the NCO down to 130 MHz.

**Do NOT use the ZJL-4HG+** in this chain. Its output would overdrive the ZHL-3A+ into compression.

### Interferometer

```
                  Fiber
HeNe ───> Lens ───> AOD ──── 1st order ───> Fiber coupler ──┐
                     │                                       │
                     └──── 0th order (block) ───x            │
                                                     Fiber BS (50/50)
           Reference arm (straight fiber) ──────────────────┘
                                                             │
                                                     Photodiode ──> Scope
```

- The AOD diffracts the beam; the 1st-order beam carries the phase modulation
- The 0th-order (undiffracted) beam should be blocked — it carries no phase info
- A fiber beamsplitter recombines the modulated arm with the reference arm
- The photodiode at the output sees the interference: bright when in-phase, dark when out-of-phase
- One photodiode is sufficient for the demo; for full BB84 you'd want detectors at both output ports

### Power and Thermal

- **RFSoC 4x2**: 12 V, up to ~10 A under load. Needs airflow.
- **ZHL-3A+**: +24 V, ~0.5-1 A draw. Current-limit the supply.
- Power both up before loading the FPGA bitstream.

## FPGA Configuration

### DAC / RFDC Settings (Vivado)

| Parameter | Value |
|-----------|-------|
| DAC Tile | Tile 0, Slice 0 |
| Sampling Rate | 4.9152 GS/s |
| Reference Clock | 491.52 MHz (on-board LMK/LMX) |
| PLL | Enabled, multiplier = 10 |
| Interpolation | 8x |
| Fabric Clock | 307.2 MHz |
| Mixer Mode | I/Q to Real |
| NCO Frequency | 150.0 MHz |
| Samples per Beat | 2 (64-bit AXI-Stream) |

### Board Controls

**Slide switches:**

| Switch | Function |
|--------|----------|
| SW3 | Mode: 0 = switch mode, 1 = register/auto mode |
| SW2 | Auto-cycle enable (when SW3=1) |
| SW1 | Cycle pattern: 0 = full 4-phase, 1 = 0/pi toggle |
| SW0 | Alice phase LSB (switch mode only) |

**Push buttons (active during auto-cycle):**

| Button | Pin | Function |
|--------|-----|----------|
| btn[3] | AT12 | Cycle unit: Hz -> KHz -> MHz |
| btn[2] | AW9 | Cycle increment: 1 -> 5 -> 10 |
| btn[1] | AV10 | Increase switching frequency |
| btn[0] | AV12 | Decrease switching frequency |

**LEDs:**

| LED | Meaning |
|-----|---------|
| led[3] | Auto-cycle active |
| led[2] | Switch mode active |
| led[1:0] | Current phase (00=0, 01=pi/2, 10=pi, 11=3pi/2) |

## Operating Procedures

### Quick Start

1. Power on RFSoC (12 V) and ZHL-3A+ (+24 V)
2. Connect RF chain: DAC0 SMA -> LPF -> SMA-BNC adapter -> ZHL-3A+ -> attenuator -> AOD
3. Load bitstream via PYNQ Jupyter (or pre-loaded if running standalone)
4. Verify 150 MHz carrier on spectrum analyzer before connecting AOD
5. Set switches: SW3=1, SW2=1, SW1=1 (auto-cycle, 0/pi toggle)
6. Align AOD Bragg angle for max 1st-order diffraction at 633 nm
7. Couple 1st-order into fiber, set up interferometer with fiber BS
8. Observe photodiode output on scope — should see square wave at switching frequency
9. Use buttons to sweep frequency and find AOD bandwidth limit

### Switch Mode (Manual Phase)

For initial alignment and verifying each phase state:

1. Set SW3=0
2. Use SW0 and SW1 to select phase:
   - 00: 0 degrees
   - 01: 90 degrees
   - 10: 180 degrees
   - 11: 270 degrees
3. Observe interference changes at the photodiode as you flip switches

### Auto-Cycle Mode (Bandwidth Sweep)

For finding the AOD's phase modulation bandwidth:

1. Set SW3=1, SW2=1
2. Set SW1=1 for 0/pi toggle (cleanest for bandwidth measurement)
3. LED[3] lights up when auto-cycle is active
4. System starts at 1 Hz — you'll see the photodiode voltage slowly toggling
5. Press btn[3] to switch to KHz, then btn[1] to increase
6. Watch the scope: the square wave gets faster, then at some point the edges round off and modulation depth decreases — that's the AOD bandwidth

### What to Expect on the Scope

At 0/pi toggle mode with a well-aligned interferometer:

- **1 Hz - 100 Hz**: Slow, clean square wave between bright and dark. Easy to see by eye.
- **1 KHz - 100 KHz**: Fast square wave. Clean transitions. This is well within AOD bandwidth.
- **100 KHz - 1 MHz**: Still clean. Acoustic transit time (~150 ns for 0.1 mm beam) isn't limiting yet.
- **1 MHz - 10 MHz**: Transitions start to soften. The acoustic wave in the crystal can't fully establish the new phase before you switch again.
- **Near the limit**: Modulation depth drops. Square wave becomes sinusoidal, then flattens. The exact frequency depends on your beam diameter through the AOD.

### Switching Speed Limit

The fundamental limit is the acoustic transit time across the laser beam:

```
t_switch = beam_diameter / v_acoustic
```

For TeO2 (v_s ~ 660 m/s):

| Beam diameter | Transit time | Max switch rate |
|---------------|-------------|-----------------|
| 2 mm (full aperture) | ~3 us | ~300 KHz |
| 0.5 mm | ~750 ns | ~1.3 MHz |
| 0.1 mm (focused) | ~150 ns | ~6 MHz |

Focus the beam tightly through the aperture to maximize switching speed.

## Register Map

| Address | Name | R/W | Description |
|---------|------|-----|-------------|
| 0x00 | CTRL | R/W | [0] global_en, [1] alice_en, [2] auto_cycle_en |
| 0x04 | ALICE_PHASE_STAGED | R/W | [1:0] Staged phase |
| 0x08 | AUTO_FREQ_HZ | R | Current switching frequency in Hz |
| 0x0C | STATUS | R | [0] alice_running, [1] auto_cycling, [2] sw_mode |
| 0x10 | PHASE_APPLY | R/W | Write 1 to latch staged phase (auto-clears) |
| 0x14 | ALICE_PHASE_ACTIVE | R | [1:0] Currently active phase |
| 0x18 | AUTO_PERIOD_TICKS | R | Period in fabric clock ticks (307.2 MHz) |
| 0x1C | VERSION | R | 0x2026_0505 |

## AOD Alignment at 633 nm

The AOD is designed for 785 nm. At 633 nm the Bragg angle changes:

```
theta_B = lambda * f / (2 * v_s)
```

At 150 MHz with v_s ~ 660 m/s: theta_B ~ 72 mrad (~4 degrees). Rotate the AOD a few degrees from its 785 nm position while monitoring the 1st-order beam.

Expect:
- Reduced diffraction efficiency (~30-50% vs ~80%+ at 785 nm)
- Ghost beams from AR coating mismatch — use an iris to clean up
- 0th-order beam always present — block it

## Extensions and Future Work

### Random Basis/Bit Selection from GGX-Sampler

The [FPGA-GGX-Sampler](../FPGA-GGX-Sampler/) project (in the parent directory) contains reusable pseudo-random number generation modules that can drive BB84 basis and bit selection:

- **`axis_hash_combine_2d.sv`** — MurmurHash3-based PRNG. Takes a 32-bit seed, produces a 32-bit hash via a 10-stage pipeline. Extract 2 bits for basis + bit selection. Best option for BB84 — good statistical properties, fast, deterministic (helpful for debugging).
- **`axis_sobol2d_stateless.sv`** — Sobol quasi-random sequence generator. Low-discrepancy sequences; stateless (index-addressable). Better for sampling than crypto, but usable for a demo.
- **`axis_nested_uniform_scramble.sv`** — Laine-Karras permutation. Adds diffusion on top of Sobol for better randomization.

All modules use AXI4-Stream interfaces and are already in synthesizable SystemVerilog. Integration path: instantiate alongside the auto-cycler, feed the 2-bit output into the phase select mux as a fourth mode.

For a cryptographically serious deployment, these pseudo-random generators would be replaced by a true quantum random number generator (QRNG) — a device that uses quantum processes (e.g., photon detection statistics, vacuum fluctuations) to produce genuinely unpredictable bits. For the demo, PRNG is fine and much easier to debug.

### ADC-Based Photon Detection

The RFSoC has 14-bit ADCs (up to 2.058 GS/s) that could digitize the photodiode output directly. This would make the board both transmitter AND receiver:

**Architecture:**
```
Photodiode ──> TIA (transimpedance amp) ──> RFSoC ADC input
                                                  │
                                           FPGA fabric
                                                  │
                                     Threshold comparator
                                                  │
                                     Timestamp counter ──> FIFO ──> PS readout
```

This is essentially a **time tagger** — record when the photodiode signal crosses a threshold, with sub-nanosecond timing resolution from the high-speed ADC. The FPGA timestamps each detection event relative to the phase switching clock, enabling coincidence detection and basis reconciliation.

Open-source FPGA time-tagger projects exist (search "FPGA TCSPC" or "FPGA time tagger"), but most target standalone FPGA boards, not RFSoC. Building one for the RFSoC with the ADC already integrated would be a meaningful open-source contribution — the analog front-end (photodiode + TIA) is the harder part; the digital logic (threshold + timestamp + FIFO) is straightforward.

### Multi-Tone Operation

The current firmware outputs a single 150 MHz carrier with phase modulation. The RFSoC can synthesize multiple simultaneous RF tones (e.g., 130 + 140 + 150 + 160 MHz) on one DAC, deflecting the AOD beam to multiple angles at once. This is standard practice for optical tweezer arrays in atomic physics but could also enable frequency-encoded QKD:

- Each BB84 state maps to a different RF frequency → different deflection angle → different spatial path
- Requires replacing the constant I/Q PSK generator with a DDS/LUT waveform engine
- Peak-to-average ratio rises with tone count — the ZHL-3A+ must be backed off from P1dB to avoid clipping

### External Clock Reference

The RFSoC's on-board reference is ~25 ppm. For atomic-physics-grade frequency stability, feed an external 10 MHz reference (rubidium or GPSDO) into the CLK_IN SMA. At 150 MHz, 25 ppm means ~3.75 KHz drift — negligible for the demo but matters for long-term stability in a real QKD deployment.
