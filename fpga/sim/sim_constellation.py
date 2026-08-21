#!/usr/bin/env python3
"""
End-to-end signal-chain model for the BB84 phase-encoded transmitter.

Models the path from the FPGA's digital I/Q output (axis_psk_gen) through the
optical Mach-Zehnder to the photodiode, under both heterodyne (Alice and Bob
at different RF frequencies) and homodyne (same frequency) readout. The
optical carrier is suppressed by working in the rotating frame at Alice's RF
frequency, so the AOM phase modulation appears directly in the complex
baseband and the heterodyne beat appears as a single complex exponential.

Produces four report figures:
  fig1_constellation.png    — clean BB84 constellation
  fig2_beat_phase_steps.png — heterodyne beat note with phase steps annotated
  fig3_homodyne_vs_het.png  — drift kills homodyne, heterodyne survives
  fig4_demod_phase.png      — demodulated phase angle stepping vs time

Run:
    python3 fpga/sim/sim_constellation.py
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, sosfiltfilt, hilbert


# ----- Phase-to-IQ mapping (matches axis_psk_gen.sv exactly) -----
PHASE_IQ = {
    0: ( 30000,      0),
    1: (     0,  30000),
    2: (-30000,      0),
    3: (     0, -30000),
}
PHASE_LABEL = {0: "0", 1: "π/2", 2: "π", 3: "3π/2"}
PHASE_DEG   = {0: 0.0, 1: 90.0, 2: 180.0, 3: 270.0}
PHASE_COLOR = {0: "#1f77b4", 1: "#ff7f0e", 2: "#2ca02c", 3: "#d62728"}
FULL_SCALE = 32768.0


def make_baseband(phases, samples_per_phase):
    """Reproduce axis_psk_gen output as a normalized complex baseband stream."""
    iq = np.zeros(len(phases) * samples_per_phase, dtype=complex)
    for i, p in enumerate(phases):
        I, Q = PHASE_IQ[p]
        iq[i * samples_per_phase : (i + 1) * samples_per_phase] = (
            (I + 1j * Q) / FULL_SCALE
        )
    return iq


def add_path_drift(field, drift_rms_rad, drift_corner_hz, fs, seed=None):
    """Slow random phase fluctuation on one arm — models acoustic + thermal drift."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(len(field))
    sos = butter(2, drift_corner_hz, "low", fs=fs, output="sos")
    drift = sosfiltfilt(sos, white)
    drift = drift / np.std(drift) * drift_rms_rad
    return field * np.exp(1j * drift)


def photodiode(alice_bb, bob_bb, beat_freq_hz, fs, drift_kwargs=None):
    """
    MZI photodiode model in Alice's rotating frame.

    Lab frame:  |E_a e^{i*w_a*t} + E_b e^{i*w_b*t}|^2
    Rotated:    |E_a + E_b e^{i*(w_b-w_a)*t}|^2

    beat_freq_hz = 0 -> homodyne; > 0 -> heterodyne.
    """
    t = np.arange(len(alice_bb)) / fs
    bob = bob_bb * np.exp(2j * np.pi * beat_freq_hz * t)
    if drift_kwargs is not None:
        bob = add_path_drift(bob, fs=fs, **drift_kwargs)
    return np.abs(alice_bb + bob) ** 2


def demod_heterodyne(pd_signal, beat_freq_hz, fs, filter_bw_hz):
    """Bandpass at beat, Hilbert, mix down to baseband."""
    f_lo = max(1.0, beat_freq_hz - filter_bw_hz)
    f_hi = min(0.49 * fs, beat_freq_hz + filter_bw_hz)
    sos = butter(4, [f_lo, f_hi], "band", fs=fs, output="sos")
    bp = sosfiltfilt(sos, pd_signal)
    analytic = hilbert(bp)
    t = np.arange(len(bp)) / fs
    return analytic * np.exp(-2j * np.pi * beat_freq_hz * t)


def add_amplitude_noise(signal, snr_db):
    """Add Gaussian noise at the specified SNR for realistic-looking constellations."""
    sig_power = np.mean(np.abs(signal) ** 2)
    noise_power = sig_power / 10 ** (snr_db / 10)
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(len(signal)) + 1j * rng.standard_normal(len(signal))
    noise *= np.sqrt(noise_power / 2)
    return signal + noise


# ============================================================
# Figure 1: clean BB84 constellation
# ============================================================
def fig1_constellation():
    """Pedagogical: ideal heterodyne demod, four clusters with realistic noise."""
    fs = 100_000.0
    beat_freq_hz = 1_000.0
    samples_per_phase = int(fs * 0.05)  # 50 ms each
    phases = [0, 1, 2, 3] * 8           # 8 reps of full cycle
    bob_phases = [0] * len(phases)

    alice = make_baseband(phases, samples_per_phase)
    bob = make_baseband(bob_phases, samples_per_phase)
    pd = photodiode(alice, bob, beat_freq_hz, fs)
    demod = demod_heterodyne(pd, beat_freq_hz, fs, filter_bw_hz=200.0)
    demod = add_amplitude_noise(demod, snr_db=18)

    fig, ax = plt.subplots(figsize=(7, 7))
    edge = int(0.20 * samples_per_phase)
    seen = set()
    for i, p in enumerate(phases):
        chunk = demod[i * samples_per_phase + edge : (i + 1) * samples_per_phase - edge]
        decim = chunk[::15]
        label = f"{PHASE_LABEL[p]}" if p not in seen else None
        seen.add(p)
        ax.scatter(decim.real, decim.imag, s=14, alpha=0.4, c=PHASE_COLOR[p],
                   edgecolors="none", label=label)

    # Centroids
    for p in [0, 1, 2, 3]:
        chunks = []
        for i, q in enumerate(phases):
            if q == p:
                chunks.append(demod[i * samples_per_phase + edge :
                                    (i + 1) * samples_per_phase - edge])
        center = np.mean(np.concatenate(chunks))
        ax.plot(center.real, center.imag, "o", mfc="white",
                mec=PHASE_COLOR[p], mew=2, ms=12, zorder=10)
        # Label well outside the cloud — fixed angular position per phase
        radius = np.abs(center) + 0.7
        angle = np.angle(center)
        ax.annotate(
            f"φ = {PHASE_LABEL[p]}",
            xy=(radius * np.cos(angle), radius * np.sin(angle)),
            fontsize=13, color=PHASE_COLOR[p], fontweight="bold",
            ha="center", va="center",
        )

    R = np.max(np.abs(demod)) * 1.15
    ax.set_xlim(-R, R)
    ax.set_ylim(-R, R)
    ax.axhline(0, color="k", lw=0.4)
    ax.axvline(0, color="k", lw=0.4)
    ax.set_aspect("equal")
    ax.set_xlabel("I", fontsize=12)
    ax.set_ylabel("Q", fontsize=12)
    ax.set_title("BB84 constellation — heterodyne demodulated I/Q\n"
                 "(four phase states resolve as four clusters at 90° spacing)",
                 fontsize=12)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig("fig1_constellation.png", dpi=150)
    plt.close(fig)
    print("Saved fig1_constellation.png")


# ============================================================
# Figure 2: beat-note time trace with phase steps
# ============================================================
def fig2_beat_phase_steps():
    """Pedagogical: show the heterodyne beat with Alice's phase visibly stepping."""
    fs = 1_000_000.0
    beat_freq_hz = 50_000.0          # 50 kHz beat → 20 µs period
    samples_per_phase = int(fs * 60e-6)  # 60 µs per phase = 3 beat cycles
    phases = [0, 1, 2, 3, 0]         # full cycle + return
    bob_phases = [0] * len(phases)

    alice = make_baseband(phases, samples_per_phase)
    bob = make_baseband(bob_phases, samples_per_phase)
    pd = photodiode(alice, bob, beat_freq_hz, fs)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    t_us = np.arange(len(pd)) / fs * 1e6  # µs
    ax.plot(t_us, pd, lw=1.0, color="#1f77b4")
    for i, p in enumerate(phases):
        x0 = i * samples_per_phase / fs * 1e6
        x1 = (i + 1) * samples_per_phase / fs * 1e6
        ax.axvspan(x0, x1, color=PHASE_COLOR[p], alpha=0.10)
        ax.text((x0 + x1) / 2, np.max(pd) * 1.01, f"Alice = {PHASE_LABEL[p]}",
                ha="center", fontsize=11, color=PHASE_COLOR[p], fontweight="bold")

    # Vertical lines at transitions
    for i in range(1, len(phases)):
        x = i * samples_per_phase / fs * 1e6
        ax.axvline(x, color="k", lw=0.6, alpha=0.4, ls="--")

    ax.set_xlabel("Time [µs]", fontsize=12)
    ax.set_ylabel("Photodiode signal [a.u.]", fontsize=12)
    ax.set_title("Heterodyne beat note (50 kHz) shows Alice's BB84 phase steps\n"
                 "(beat amplitude is constant; phase shifts at each transition)",
                 fontsize=12)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig("fig2_beat_phase_steps.png", dpi=150)
    plt.close(fig)
    print("Saved fig2_beat_phase_steps.png")


# ============================================================
# Figure 3: homodyne vs heterodyne under drift
# ============================================================
def fig3_homodyne_vs_heterodyne():
    """
    Mix lab + pedagogical:
      Homodyne uses a 500 Hz "would-have-been" beat (zeroed) under realistic drift.
      Heterodyne uses 500 Hz beat — what you actually had in the lab.
    """
    fs_homo = 100_000.0
    fs_het = 1_000_000.0
    drift = dict(drift_rms_rad=2.5, drift_corner_hz=200.0, seed=0)

    # ---- Homodyne ----
    samples_per_phase_homo = int(fs_homo * 0.005)  # 5 ms per phase
    phases_homo = [0, 2, 0, 2, 0, 2, 0, 2]         # 0/π toggle, like SW1=1
    bob_homo = [0] * len(phases_homo)
    alice_homo = make_baseband(phases_homo, samples_per_phase_homo)
    bob_homo_bb = make_baseband(bob_homo, samples_per_phase_homo)
    pd_homo = photodiode(alice_homo, bob_homo_bb, 0.0, fs_homo, drift_kwargs=drift)

    # ---- Heterodyne ----
    beat_het = 50_000.0  # exaggerated beat for visibility (lab was 500 Hz)
    samples_per_phase_het = int(fs_het * 60e-6)
    phases_het = [0, 2, 0, 2, 0]
    bob_het = [0] * len(phases_het)
    alice_het = make_baseband(phases_het, samples_per_phase_het)
    bob_het_bb = make_baseband(bob_het, samples_per_phase_het)
    pd_het = photodiode(alice_het, bob_het_bb, beat_het, fs_het, drift_kwargs=drift)

    fig, (ax_h, ax_e) = plt.subplots(2, 1, figsize=(12, 8))

    # Homodyne
    t_h = np.arange(len(pd_homo)) / fs_homo * 1e3  # ms
    ax_h.plot(t_h, pd_homo, lw=0.8, color="#d62728")
    for i, p in enumerate(phases_homo):
        x0 = i * samples_per_phase_homo / fs_homo * 1e3
        x1 = (i + 1) * samples_per_phase_homo / fs_homo * 1e3
        ax_h.axvspan(x0, x1, color=PHASE_COLOR[p], alpha=0.08)
        if i < 4:
            ax_h.text((x0 + x1) / 2, np.max(pd_homo) * 0.97, PHASE_LABEL[p],
                      ha="center", fontsize=10, color=PHASE_COLOR[p], fontweight="bold")
    ax_h.set_xlabel("Time [ms]")
    ax_h.set_ylabel("PD signal [a.u.]")
    ax_h.set_title("Homodyne (Alice = Bob frequency) under path-length drift\n"
                   "amplitude wanders independently of Alice's phase — encoding lost",
                   fontsize=11)
    ax_h.grid(True, alpha=0.3)

    # Heterodyne
    t_e = np.arange(len(pd_het)) / fs_het * 1e6  # µs
    ax_e.plot(t_e, pd_het, lw=0.9, color="#1f77b4")
    for i, p in enumerate(phases_het):
        x0 = i * samples_per_phase_het / fs_het * 1e6
        x1 = (i + 1) * samples_per_phase_het / fs_het * 1e6
        ax_e.axvspan(x0, x1, color=PHASE_COLOR[p], alpha=0.10)
        ax_e.text((x0 + x1) / 2, np.max(pd_het) * 1.01, PHASE_LABEL[p],
                  ha="center", fontsize=10, color=PHASE_COLOR[p], fontweight="bold")
        if i > 0:
            ax_e.axvline(x0, color="k", lw=0.6, alpha=0.4, ls="--")
    ax_e.set_xlabel("Time [µs]")
    ax_e.set_ylabel("PD signal [a.u.]")
    ax_e.set_title("Heterodyne (50 kHz beat) under same drift\n"
                   "beat amplitude is steady; phase steps at each Alice transition",
                   fontsize=11)
    ax_e.grid(True, alpha=0.3)

    fig.suptitle("Path-length drift kills homodyne, heterodyne survives",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig("fig3_homodyne_vs_het.png", dpi=150)
    plt.close(fig)
    print("Saved fig3_homodyne_vs_het.png")


# ============================================================
# Figure 4: demodulated phase angle vs time
# ============================================================
def fig4_demod_phase():
    """Demod phase as a step function — same data as the constellation, time-domain view."""
    fs = 1_000_000.0
    beat_freq_hz = 50_000.0
    samples_per_phase = int(fs * 200e-6)  # 200 µs per phase = 10 beat cycles
    phases = [0, 1, 2, 3, 0, 2, 1, 3]     # show all transition types
    bob_phases = [0] * len(phases)

    alice = make_baseband(phases, samples_per_phase)
    bob = make_baseband(bob_phases, samples_per_phase)
    pd = photodiode(alice, bob, beat_freq_hz, fs)
    demod = demod_heterodyne(pd, beat_freq_hz, fs, filter_bw_hz=20_000.0)

    # Re-wrap to [-90, 270] so the φ=π level (180°) doesn't straddle the
    # ±180° branch cut of np.angle and produce visual jitter spikes.
    phi = np.degrees(np.angle(demod))
    phi = np.where(phi < -90.0, phi + 360.0, phi)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    t_us = np.arange(len(phi)) / fs * 1e6
    ax.plot(t_us, phi, lw=1.0, color="#1f77b4")

    # Mark expected level for each phase state
    expected_deg = {0: 0, 1: 270, 2: 180, 3: 90}  # demod gives -phi_alice (mod 360)
    for i, p in enumerate(phases):
        x0 = i * samples_per_phase / fs * 1e6
        x1 = (i + 1) * samples_per_phase / fs * 1e6
        ax.axvspan(x0, x1, color=PHASE_COLOR[p], alpha=0.10)
        ax.hlines(expected_deg[p], x0, x1, colors=PHASE_COLOR[p],
                  linestyles="dashed", lw=1.5)
        ax.text((x0 + x1) / 2, 165, f"φ = {PHASE_LABEL[p]}", ha="center",
                fontsize=10, color=PHASE_COLOR[p], fontweight="bold")
        if i > 0:
            ax.axvline(x0, color="k", lw=0.5, alpha=0.4, ls="--")

    ax.set_xlabel("Time [µs]", fontsize=12)
    ax.set_ylabel("Demodulated phase [deg]", fontsize=12)
    ax.set_yticks([0, 90, 180, 270])
    ax.set_ylim(-30, 320)
    ax.set_title("Demodulated phase angle steps between four discrete BB84 levels\n"
                 "(dashed lines = expected level for each Alice phase setting)",
                 fontsize=12)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig("fig4_demod_phase.png", dpi=150)
    plt.close(fig)
    print("Saved fig4_demod_phase.png")


if __name__ == "__main__":
    fig1_constellation()
    fig2_beat_phase_steps()
    fig3_homodyne_vs_heterodyne()
    fig4_demod_phase()
