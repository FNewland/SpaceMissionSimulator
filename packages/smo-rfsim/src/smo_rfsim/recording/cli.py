"""CLI: generate a simulated baseband .cf32 recording.

    smo-rfsim-record --mod BPSK --baud 9600 --duration 30 \
        --snr 14 --carrier-offset -67000 --doppler-peak 9500 \
        --sat EOSAT1-SIM --out ./sim_captures

Run ``smo-rfsim-record --help`` for the full option list. Every impairment is
tunable; sensible RTL-SDR / LEO defaults are used otherwise. The output pair
(``.cf32`` + ``.json``) drops straight into the same analysis tooling as a real
recording.
"""
from __future__ import annotations

import argparse
import json
import sys

from .iq_recorder import BasebandRecorder, RecorderConfig, ImpairmentConfig


def _parse_cw(s: str):
    """Parse a 'freqOffsetHz:powerdB' CW-interferer spec."""
    foff, pdb = s.split(":")
    return (float(foff), float(pdb))


def build_config(a: argparse.Namespace) -> RecorderConfig:
    imp = ImpairmentConfig(
        carrier_offset_hz=a.carrier_offset,
        doppler_model=a.doppler_model,
        doppler_peak_hz=a.doppler_peak,
        doppler_constant_hz=a.doppler_constant,
        envelope_model=a.envelope,
        envelope_depth_db=a.envelope_depth,
        burst_enabled=a.bursts,
        burst_period_s=a.burst_period,
        burst_duty=a.burst_duty,
        phase_noise_rad=a.phase_noise,
        iq_gain_imbalance_db=a.iq_gain,
        iq_phase_imbalance_deg=a.iq_phase,
        dc_offset=a.dc_offset,
        residual_doppler_hz=a.residual_doppler,
        residual_doppler_jitter_hz=a.residual_jitter,
        snr_db=a.snr,
        sky_noise_fraction=a.sky_noise_frac,
        sky_noise_slope=a.sky_slope,
        impulsive_prob=a.impulsive_prob,
        impulsive_gain=a.impulsive_gain,
        quantization_bits=a.quant_bits,
        phase_noise_model=a.phase_noise_model,
        phase_noise_level_dbc_1k=a.pn_level,
        phase_noise_floor_dbc=a.pn_floor,
        multipath_enabled=a.multipath,
        ricean_k_db=a.ricean_k,
        multipath_delay_us=a.multipath_delay_us,
        fading_rate_hz=a.fading_rate,
        cw_interferers=tuple(a.cw or ()),
        broadband_rfi_db=a.broadband_rfi,
        band_edge_filter=a.band_edge,
        sample_clock_ppm=a.clock_ppm,
    )
    return RecorderConfig(
        modulation=a.mod, symbol_rate_hz=a.baud, rolloff=a.rolloff,
        gmsk_bt=a.bt, sps_internal=a.sps, sample_rate_hz=a.fs,
        duration_s=a.duration, data_source=a.data, frame_length=a.frame_length,
        random_seed=a.seed, satellite=a.sat, norad=a.norad,
        nominal_downlink_hz=a.freq, mode_label=a.mode_label, impairments=imp,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="smo-rfsim-record",
        description="Generate a simulated baseband .cf32 recording (+ sidecar).")
    # signal
    p.add_argument("--mod", default="BPSK",
                   help="modulation: BPSK/QPSK/8PSK/OQPSK/16-APSK/GMSK/GFSK (default BPSK)")
    p.add_argument("--baud", type=float, default=9600.0, help="symbol rate (Bd)")
    p.add_argument("--rolloff", type=float, default=0.35, help="RRC rolloff")
    p.add_argument("--bt", type=float, default=0.5, help="Gaussian BT (GMSK/GFSK)")
    p.add_argument("--sps", type=int, default=8, help="modulator oversampling")
    p.add_argument("--fs", type=float, default=250000.0, help="output sample rate (Hz)")
    p.add_argument("--duration", type=float, default=30.0, help="seconds")
    p.add_argument("--data", default="ccsds", choices=["ccsds", "random"],
                   help="data source")
    p.add_argument("--frame-length", type=int, default=223, help="TM frame length")
    p.add_argument("--seed", type=int, default=0, help="RNG seed (reproducible)")
    # impairments
    p.add_argument("--snr", type=float, default=15.0, help="in-band SNR (dB)")
    p.add_argument("--carrier-offset", type=float, default=-60000.0,
                   help="signal offset from DC (Hz); real captures ≈ -50k..-70k")
    p.add_argument("--doppler-model", default="leo_scurve",
                   choices=["leo_scurve", "constant", "none"])
    p.add_argument("--doppler-peak", type=float, default=9500.0,
                   help="±peak Doppler over pass (Hz)")
    p.add_argument("--doppler-constant", type=float, default=0.0)
    p.add_argument("--envelope", default="elevation_hump",
                   choices=["elevation_hump", "flat"])
    p.add_argument("--envelope-depth", type=float, default=12.0,
                   help="AOS/LOS depth below TCA peak (dB)")
    p.add_argument("--bursts", action="store_true", help="enable burst gating")
    p.add_argument("--burst-period", type=float, default=1.0)
    p.add_argument("--burst-duty", type=float, default=0.3)
    p.add_argument("--phase-noise", type=float, default=0.02,
                   help="per-sample random-walk phase std (rad)")
    p.add_argument("--iq-gain", type=float, default=0.3, help="IQ gain imbalance (dB)")
    p.add_argument("--iq-phase", type=float, default=1.5, help="IQ phase imbalance (deg)")
    p.add_argument("--dc-offset", type=float, default=0.01, help="DC offset (frac of rms)")
    p.add_argument("--quant-bits", type=int, default=8,
                   help="ADC bits (0 = float, 8 = RTL-SDR)")
    # residual Doppler
    p.add_argument("--residual-doppler", type=float, default=0.0,
                   help="residual carrier offset after correction (Hz)")
    p.add_argument("--residual-jitter", type=float, default=0.0,
                   help="AFC/PLL tracking jitter (Hz rms)")
    # colored / impulsive noise
    p.add_argument("--sky-noise-frac", type=float, default=0.0,
                   help="fraction of noise power that is colored galactic (0-1)")
    p.add_argument("--sky-slope", type=float, default=2.5,
                   help="galactic noise PSD slope f^-slope (VHF ≈ 2.5-2.8)")
    p.add_argument("--impulsive-prob", type=float, default=0.0,
                   help="per-sample probability of an impulsive noise spike")
    p.add_argument("--impulsive-gain", type=float, default=8.0,
                   help="impulse amplitude (× noise std)")
    # phase noise
    p.add_argument("--phase-noise-model", default="randwalk",
                   choices=["randwalk", "powerlaw", "none"])
    p.add_argument("--pn-level", type=float, default=-80.0,
                   help="phase noise at 1 kHz offset (dBc/Hz, powerlaw model)")
    p.add_argument("--pn-floor", type=float, default=-140.0,
                   help="phase noise far-out floor (dBc/Hz, powerlaw model)")
    # multipath
    p.add_argument("--multipath", action="store_true", help="enable Ricean multipath")
    p.add_argument("--ricean-k", type=float, default=12.0, help="Ricean K-factor (dB)")
    p.add_argument("--multipath-delay-us", type=float, default=0.0,
                   help="echo delay (µs); 0 = flat fading")
    p.add_argument("--fading-rate", type=float, default=0.0,
                   help="slow fading rate (Hz); 0 = static")
    # interference
    p.add_argument("--cw", action="append", type=_parse_cw, default=None,
                   metavar="FOFF:PDB", help="add a CW interferer 'offsetHz:powerdB' (repeatable)")
    p.add_argument("--broadband-rfi", type=float, default=-999.0,
                   help="wideband RFI raise (dB rel signal); off by default")
    # front-end
    p.add_argument("--band-edge", action="store_true",
                   help="apply RTL-SDR band-edge roll-off (keep central 4/5)")
    p.add_argument("--clock-ppm", type=float, default=0.0,
                   help="sample-clock error (ppm)")
    # provenance / output
    p.add_argument("--sat", default="EOSAT1-SIM", help="satellite name")
    p.add_argument("--norad", type=int, default=0)
    p.add_argument("--freq", type=float, default=401_415_000.0,
                   help="nominal downlink (Hz)")
    p.add_argument("--mode-label", default="", help="free-text mode label")
    p.add_argument("--out", default="./sim_captures", help="output directory")
    p.add_argument("--name", default=None, help="basename (default auto NAME_NORAD_UTC_FREQ)")
    a = p.parse_args(argv)

    cfg = build_config(a)
    rec = BasebandRecorder(cfg)
    manifest = rec.record(a.out, basename=a.name)
    print(json.dumps({k: v for k, v in manifest.items() if k != "sidecar"}, indent=2))
    print("diagnostics:", json.dumps(manifest["sidecar"]["simulation"]["diagnostics"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
