"""SMO Common — Subsystem Model ABC.

All spacecraft subsystem models implement this interface.
The SimulationEngine loads models via the registry and calls
these methods in a uniform way.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class SubsystemModel(ABC):
    """Abstract base class for all spacecraft subsystem simulation models."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Subsystem identifier (e.g. 'eps', 'aocs')."""
        ...

    @abstractmethod
    def configure(self, config: dict[str, Any]) -> None:
        """Apply configuration from the subsystem YAML file.

        Called once during engine initialisation.
        """
        ...

    @abstractmethod
    def tick(self, dt: float, orbit_state: Any, shared_params: dict[int, float]) -> None:
        """Advance the model by dt seconds.

        Args:
            dt: Simulation time step in seconds.
            orbit_state: Current OrbitState from the propagator.
            shared_params: Shared parameter store (param_id -> value).
                          The model should write its own parameters here.
        """
        ...

    @abstractmethod
    def get_telemetry(self) -> dict[int, float]:
        """Return current parameter values as {param_id: value}."""
        ...

    @abstractmethod
    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Process a command directed at this subsystem.

        Args:
            cmd: Command dict with at least 'command' key.

        Returns:
            Result dict with 'success' bool and optional 'message'.
        """
        ...

    @abstractmethod
    def inject_failure(self, failure: str, magnitude: float = 1.0, **kwargs) -> None:
        """Inject a failure into this subsystem.

        Args:
            failure: Failure type identifier.
            magnitude: Severity 0.0-1.0.
        """
        ...

    @abstractmethod
    def clear_failure(self, failure: str, **kwargs) -> None:
        """Clear a previously injected failure."""
        ...

    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        """Serialise complete subsystem state for breakpoint save."""
        ...

    @abstractmethod
    def set_state(self, state: dict[str, Any]) -> None:
        """Restore subsystem state from a breakpoint."""
        ...
