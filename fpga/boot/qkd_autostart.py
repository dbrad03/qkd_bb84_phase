#!/usr/bin/env python3
"""
QKD Phase-BB84 auto-start script.

Runs on boot to configure the RFSoC PL and RFDC so the board operates
standalone without Jupyter or ethernet. After this script completes,
switches and buttons control everything from the PL.

Setup (run once while connected via SSH or Jupyter):
    sudo cp qkd_autostart.py /usr/local/bin/
    sudo cp qkd_autostart.service /etc/systemd/system/
    sudo cp /path/to/qkd_phase_bb84.bit /home/xilinx/qkd_phase_bb84.bit
    sudo cp /path/to/qkd_phase_bb84.hwh /home/xilinx/qkd_phase_bb84.hwh
    sudo systemctl enable qkd_autostart
    sudo systemctl start qkd_autostart   # test it now
    sudo reboot                           # verify it runs on boot

The bitstream (.bit) and hardware handoff (.hwh) must both be in
/home/xilinx/ with the same base name.
"""

import sys
import time

BITSTREAM_PATH = "/home/xilinx/qkd_phase_bb84.bit"
NCO_FREQ_MHZ = 150.0
DAC_TILE_IDX = 0
DAC_BLOCK_IDX = 0

# Register offsets
REG_CTRL = 0x00
REG_VERSION = 0x1C


def main():
    print("[qkd] Starting QKD auto-configuration...")

    # Step 1: Reset PL and set reference clocks
    from pynq import PL
    PL.reset()

    import xrfclk
    xrfclk.set_ref_clks()
    print("[qkd] Reference clocks configured")

    # Step 2: Load bitstream
    from pynq import Overlay, MMIO
    ol = Overlay(BITSTREAM_PATH)
    print(f"[qkd] Bitstream loaded: {BITSTREAM_PATH}")

    # Step 3: Get MMIO handle to QKD registers
    qkd_info = ol.ip_dict['qkd_top_wrapper_bd_0']
    base_addr = qkd_info['phys_addr']
    addr_range = qkd_info['addr_range']
    mmio = MMIO(base_addr, addr_range)

    version = mmio.read(REG_VERSION)
    print(f"[qkd] VERSION: {version:#010x}")
    if version != 0x2026_0505:
        print(f"[qkd] WARNING: unexpected version (expected 0x20260505)")

    # Step 4: Configure RF-DAC
    import xrfdc
    rf = ol.usp_rf_data_converter_0

    dac_tile = rf.dac_tiles[DAC_TILE_IDX]
    print(f"[qkd] DAC Tile {DAC_TILE_IDX}: PLL locked = {dac_tile.PLLLockStatus}")

    block = dac_tile.blocks[DAC_BLOCK_IDX]
    block.MixerSettings['Freq'] = NCO_FREQ_MHZ
    block.UpdateEvent(xrfdc.EVENT_MIXER)
    print(f"[qkd] NCO set to {NCO_FREQ_MHZ} MHz")

    # Step 5: Enable outputs (global_en + alice_en, no auto_cycle_en)
    # Auto-cycle is controlled by SW2 on the board
    mmio.write(REG_CTRL, 0x03)
    print("[qkd] Outputs enabled (CTRL=0x03)")

    print("[qkd] Ready. Use board switches and buttons to operate.")
    print("[qkd]   SW3=0: switch mode | SW3=1,SW2=1: auto-cycle")
    print("[qkd]   SW1=0: full 4-phase | SW1=1: 0/pi toggle")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[qkd] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
