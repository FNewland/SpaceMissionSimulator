"""SMO Common — Eclipse Detection Utilities."""
import math
import numpy as np


def is_in_eclipse(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> bool:
    """Cylindrical Earth shadow model for eclipse detection."""
    r_mag = np.linalg.norm(sc_pos_eci)
    sun_hat = sun_pos_eci / (np.linalg.norm(sun_pos_eci) + 1e-9)
    dot = np.dot(sc_pos_eci, sun_hat)
    perp = math.sqrt(max(r_mag * r_mag - dot * dot, 0.0))
    return (dot < 0.0) and (perp < earth_radius_km)


def eclipse_fraction(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> float:
    """Return eclipse fraction (0.0 = full sun, 1.0 = full shadow).

    Uses penumbra model for partial eclipses near shadow boundary.
    """
    r_mag = np.linalg.norm(sc_pos_eci)
    sun_hat = sun_pos_eci / (np.linalg.norm(sun_pos_eci) + 1e-9)
    dot = np.dot(sc_pos_eci, sun_hat)

    if dot >= 0.0:
        return 0.0  # sunlit side

    perp = math.sqrt(max(r_mag * r_mag - dot * dot, 0.0))

    if perp >= earth_radius_km:
        return 0.0  # outside shadow cylinder

    # Penumbra transition zone (~50 km)
    penumbra_width = 50.0  # km
    if perp > earth_radius_km - penumbra_width:
        return (earth_radius_km - perp) / penumbra_width

    return 1.0  # full shadow
