"""Continuous baseband signal processing pipeline.

Three threads — TX, Channel, RX — connected by sample buffers.
The TX generates a continuous stream of modulated CCSDS frames
(real data or idle fill). The channel applies AWGN and Doppler.
The RX performs real carrier/clock recovery, FEC decoding, and
frame synchronization.
"""
