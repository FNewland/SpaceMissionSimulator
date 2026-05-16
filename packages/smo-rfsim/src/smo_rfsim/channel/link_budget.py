"""CCSDS 401.0-B Space Link Budget Calculator.

Computes received signal power, system noise temperature, and Eb/N0
for a LEO spacecraft-to-ground link. All values in SI + dB units.

Reference: CCSDS 401.0-B-32 (RF and Modulation Systems)
"""

import math
import logging

logger = logging.getLogger(__name__)

# Physical constants
C_M_S = 299_792_458.0        # speed of light (m/s)
K_BOLTZMANN = 1.380649e-23   # Boltzmann constant (J/K)
K_DB = -228.599              # 10*log10(k) in dBW/K/Hz


class LinkBudget:
    """Computes instantaneous Eb/N0 from link geometry and equipment parameters.

    Parameters match a typical LEO cubesat S-band or UHF downlink.
    """

    def __init__(self,
                 eirp_dbw: float = 0.0,
                 frequency_hz: float = 437.0e6,
                 gs_gt_dbk: float = 12.0,
                 atmospheric_loss_db: float = 0.5,
                 implementation_loss_db: float = 2.0,
                 polarization_loss_db: float = 0.3,
                 coding_gain_db: float = 6.0):
        self.eirp_dbw = eirp_dbw
        self.frequency_hz = frequency_hz
        self.gs_gt_dbk = gs_gt_dbk
        self.atmospheric_loss_db = atmospheric_loss_db
        self.implementation_loss_db = implementation_loss_db
        self.polarization_loss_db = polarization_loss_db
        self.coding_gain_db = coding_gain_db

    def fspl_db(self, range_km: float) -> float:
        """Free-space path loss (dB) for given slant range."""
        if range_km <= 0:
            return 0.0
        range_m = range_km * 1000.0
        # FSPL = 20*log10(4*pi*d*f/c)
        return (20.0 * math.log10(range_m)
                + 20.0 * math.log10(self.frequency_hz)
                - 147.55)

    def doppler_hz(self, range_rate_m_s: float) -> float:
        """Doppler shift (Hz) from range rate."""
        return -self.frequency_hz * range_rate_m_s / C_M_S

    def propagation_delay_s(self, range_km: float) -> float:
        """One-way propagation delay (seconds)."""
        return range_km * 1000.0 / C_M_S

    def antenna_pointing_loss_db(self, off_axis_deg: float,
                                  beamwidth_deg: float = 90.0) -> float:
        """Antenna gain reduction for off-axis pointing (cosine^n model).

        For a cubesat with hemispherical coverage, beamwidth ~90°.
        """
        if beamwidth_deg <= 0 or off_axis_deg <= 0:
            return 0.0
        # 3dB beamwidth → n = log(0.5) / log(cos(BW/2))
        cos_half_bw = math.cos(math.radians(beamwidth_deg / 2.0))
        if cos_half_bw >= 1.0 or cos_half_bw <= 0.0:
            return 0.0
        n = math.log(0.5) / math.log(cos_half_bw)
        cos_theta = math.cos(math.radians(min(off_axis_deg, 89.0)))
        if cos_theta <= 0:
            return 30.0  # beyond horizon
        return -10.0 * n * math.log10(max(cos_theta, 1e-6))

    def compute(self, range_km: float, data_rate_bps: float,
                elevation_deg: float = 90.0,
                off_axis_deg: float = 0.0) -> dict:
        """Compute full link budget for current geometry.

        Returns dict with all link budget components and final Eb/N0.
        """
        fspl = self.fspl_db(range_km)
        pointing = self.antenna_pointing_loss_db(off_axis_deg)

        # Elevation-dependent atmospheric loss (airmass model)
        if elevation_deg > 5.0:
            airmass = 1.0 / math.sin(math.radians(elevation_deg))
        else:
            airmass = 10.0  # below 5° elevation
        atm_loss = self.atmospheric_loss_db * min(airmass, 10.0)

        # Total path loss
        total_loss = fspl + atm_loss + self.polarization_loss_db + pointing

        # Received C/N0 (carrier to noise density ratio)
        # C/N0 = EIRP - L_total + G/T - k
        cn0_dbhz = (self.eirp_dbw - total_loss + self.gs_gt_dbk - K_DB)

        # Eb/N0 = C/N0 - 10*log10(Rb)
        if data_rate_bps > 0:
            eb_n0_db = cn0_dbhz - 10.0 * math.log10(data_rate_bps)
        else:
            eb_n0_db = 0.0

        # Subtract implementation losses, add coding gain
        eb_n0_db = eb_n0_db - self.implementation_loss_db + self.coding_gain_db

        # Link margin = Eb/N0 - required Eb/N0 (assume 9.6 dB for 10^-5 BER BPSK)
        margin_db = eb_n0_db - 9.6

        # Propagation delay
        delay_s = self.propagation_delay_s(range_km)

        return {
            "eirp_dbw": self.eirp_dbw,
            "fspl_db": fspl,
            "atmospheric_loss_db": atm_loss,
            "pointing_loss_db": pointing,
            "polarization_loss_db": self.polarization_loss_db,
            "total_loss_db": total_loss,
            "gs_gt_dbk": self.gs_gt_dbk,
            "cn0_dbhz": cn0_dbhz,
            "data_rate_bps": data_rate_bps,
            "eb_n0_db": eb_n0_db,
            "coding_gain_db": self.coding_gain_db,
            "implementation_loss_db": self.implementation_loss_db,
            "margin_db": margin_db,
            "range_km": range_km,
            "elevation_deg": elevation_deg,
            "delay_s": delay_s,
            "frequency_hz": self.frequency_hz,
        }
