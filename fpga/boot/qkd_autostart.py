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

BITSTREAM_PATH = "/home/xilinx/qkd_phase_bb84.bit"
ALICE_NCO_MHZ = 100.0
BOB_NCO_MHZ   = 90.0

REG_CTRL = 0x00
REG_VERSION = 0x1C


def main():
    print("[qkd] Starting QKD auto-configuration...")

    from pynq import PL
    PL.reset()

    import xrfclk
    xrfclk.set_ref_clks()
    print("[qkd] Reference clocks configured")

    from pynq import Overlay, MMIO
    ol = Overlay(BITSTREAM_PATH)
    print(f"[qkd] Bitstream loaded: {BITSTREAM_PATH}")

    qkd_info = ol.ip_dict['qkd_top_wrapper_bd_0']
    mmio = MMIO(qkd_info['phys_addr'], qkd_info['addr_range'])

    version = mmio.read(REG_VERSION)
    print(f"[qkd] VERSION: {version:#010x}")
    if version != 0x2026_0508:
        print(f"[qkd] WARNING: unexpected version (expected 0x20260508)")

    import xrfdc
    rf = ol.usp_rf_data_converter_0

    # Alice — DAC Tile 0 (Tile 228)
    alice_tile = rf.dac_tiles[0]
    print(f"[qkd] Alice Tile 0: PLL locked = {alice_tile.PLLLockStatus}")
    alice_tile.blocks[0].MixerSettings['Freq'] = ALICE_NCO_MHZ
    alice_tile.blocks[0].UpdateEvent(xrfdc.EVENT_MIXER)
    print(f"[qkd] Alice NCO: {ALICE_NCO_MHZ} MHz")

    # Bob — DAC Tile 2 (Tile 230)
    bob_tile = rf.dac_tiles[2]
    print(f"[qkd] Bob Tile 2: PLL locked = {bob_tile.PLLLockStatus}")
    bob_tile.blocks[0].MixerSettings['Freq'] = BOB_NCO_MHZ
    bob_tile.blocks[0].UpdateEvent(xrfdc.EVENT_MIXER)
    print(f"[qkd] Bob NCO: {BOB_NCO_MHZ} MHz")

    # Enable both channels: global_en + alice_en + bob_en
    mmio.write(REG_CTRL, 0x07)
    print("[qkd] Both channels enabled (CTRL=0x07)")

    print("[qkd] Ready. Use board switches and buttons to operate.")
    print("[qkd]   SW3=0: switch mode | SW3=1,SW2=1: auto-cycle")
    print("[qkd]   SW1=0: full 4-phase | SW1=1: 0/pi toggle")
    print("[qkd]   CTRL: 0x03=Alice, 0x05=Bob, 0x07=both, 0x0B=Alice+auto")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[qkd] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
