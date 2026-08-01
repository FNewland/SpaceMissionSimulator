"""smo_rfsim.recording — baseband I/Q recorder.

Exports the simulator's modulated baseband as an interleaved-float32 ``.cf32``
file (identical layout to a GNU Radio complex File Sink and to the RTL-SDR
captures in the RTLSDR/ dataset), with a JSON sidecar and optional SigMF
metadata, so a simulated downlink can be compared directly against real
spacecraft recordings.

This package is additive: it does not modify the live bridge/pipeline. It
reuses the existing ``dsp.Modulator`` and CCSDS framing to build the signal,
then applies configurable real-world SDR impairments (time-varying Doppler,
pass amplitude envelope, carrier offset, phase noise, IQ imbalance, DC offset,
8-bit quantization, wideband AWGN floor).
"""
from .iq_recorder import (
    BasebandRecorder,
    RecorderConfig,
    ImpairmentConfig,
    write_cf32,
)

__all__ = [
    "BasebandRecorder",
    "RecorderConfig",
    "ImpairmentConfig",
    "write_cf32",
]
