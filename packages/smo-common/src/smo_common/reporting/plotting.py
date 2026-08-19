"""One place that decides whether this process can draw a plot, and why not.

``smo-common[reports]`` is an optional extra: the report generator degrades to
a stated notice when matplotlib is absent instead of failing the whole report.
That contract is right; the *diagnostic* that came with it was not.

Two things went wrong in the field and both are fixed here.

1. **The message told the operator to run a command they had already run.**
   "matplotlib is not installed. Install it with ``pip install
   smo-common[reports]``" is unfalsifiable from the outside: the overwhelmingly
   common cause is that the server is running under a *different interpreter*
   than the one ``pip`` installed into (a system Python vs ``.venv``, or the
   ``py`` launcher picking another Python on Windows), and nothing on screen
   said which interpreter that was. :func:`unavailable_message` names
   ``sys.executable``, the environment, and the actual exception, and quotes a
   ``pip`` command pinned to *this* interpreter.

2. **The import guard was broad enough to swallow a real failure.**
   ``except ImportError`` around ``import matplotlib`` also catches an
   ImportError raised from *inside* a matplotlib that is genuinely installed —
   a broken C extension, a NumPy ABI mismatch, or a backend that cannot be
   loaded. All of those were reported as "matplotlib is not installed" on a
   machine where it plainly was.

   The backend is the classic instance. ``matplotlib.use("Agg", force=False)``
   *silently ignores* a failed switch (that is what ``force=False`` means) and,
   as a side effect, sets ``rcParams['backend_fallback'] = False`` — which
   disables matplotlib's own headless fallback to Agg. On a machine with no
   display, or with ``MPLBACKEND``/``matplotlibrc`` naming an interactive
   backend, the import then raises and the report claims matplotlib is missing.
   This module pins ``MPLBACKEND=Agg`` before matplotlib is imported at all and
   calls ``use("Agg", force=True)``, so the non-interactive backend is chosen
   deterministically on a Mac, a PC and a headless server alike, and a genuine
   failure is reported as a failure.

The probe also *creates and closes a figure*, because a backend that imports
fine can still fail on first figure creation — the exact case that would
otherwise escape the import guard and reach an operator as a 500.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "PlottingStatus",
    "plotting_status",
    "require_plotting",
    "unavailable_message",
    "unavailable_html",
    "log_plotting_status",
    "PlottingUnavailable",
]

#: The one backend this suite ever uses. Non-interactive, present in every
#: matplotlib build, identical behaviour on macOS, Windows and Linux.
BACKEND = "Agg"

_EXTRA = 'smo-common[reports]'


class PlottingUnavailable(RuntimeError):
    """Raised by :func:`require_plotting` when plots cannot be drawn."""

    def __init__(self, status: "PlottingStatus"):
        super().__init__(status.summary())
        self.status = status


@dataclass
class PlottingStatus:
    """What this interpreter can actually do, and the evidence for it."""

    available: bool
    #: ``"ok"`` | ``"not-installed"`` | ``"import-failed"`` | ``"backend-failed"``
    state: str
    error: str = ""
    version: str = ""
    backend: str = ""
    executable: str = field(default_factory=lambda: sys.executable or "?")
    prefix: str = field(default_factory=lambda: sys.prefix)
    venv: str = field(default_factory=lambda: os.environ.get("VIRTUAL_ENV", ""))
    mplbackend: str = field(
        default_factory=lambda: os.environ.get("MPLBACKEND", ""))

    # ── operator-facing text ──────────────────────────────────────

    def pip_command(self) -> str:
        """A ``pip`` line pinned to THIS interpreter.

        ``pip install "smo-common[reports]"`` is ambiguous on any machine with
        more than one Python, which is every machine. ``<executable> -m pip``
        is not.

        And the extra form is WORSE than ambiguous when smo-common is an
        editable/local install (the normal case for this suite): pip resolves
        the bare name ``smo-common`` against PyPI, where it does not exist, so
        the command fails and the operator concludes the fix "did not work".
        Detect the editable install and prescribe the dependency directly.
        """
        exe = self.executable or sys.executable or "python"
        quoted = f'"{exe}"' if " " in exe else exe
        if self._smo_common_is_editable():
            # Installing the extra by name cannot work for a local package
            # that is not on an index; matplotlib is the only thing the
            # [reports] extra pulls in, so prescribe it directly.
            return f'{quoted} -m pip install matplotlib'
        return f'{quoted} -m pip install "{_EXTRA}"'

    @staticmethod
    def _smo_common_is_editable() -> bool:
        """True when smo-common is installed editable (from a local tree)."""
        try:
            from importlib.metadata import distribution
            import json as _json
            dist = distribution("smo-common")
            txt = dist.read_text("direct_url.json")
            if txt:
                info = _json.loads(txt)
                if info.get("dir_info", {}).get("editable"):
                    return True
                # A local directory install (even non-editable) is equally
                # unreachable via the index; treat it the same.
                if str(info.get("url", "")).startswith("file:"):
                    return True
        except Exception:
            pass
        return False

    def environment(self) -> str:
        return self.venv or self.prefix or "?"

    def summary(self) -> str:
        """One line, for a log or a status field."""
        if self.available:
            return (f"matplotlib {self.version} ({self.backend} backend) "
                    f"under {self.executable}")
        return (f"{self.headline()} — interpreter {self.executable}, "
                f"environment {self.environment()}: {self.error}")

    def headline(self) -> str:
        return {
            "not-installed": "matplotlib is not installed in THIS interpreter",
            "import-failed": "matplotlib is installed but failed to import",
            "backend-failed": (
                f"matplotlib is installed but its {BACKEND} backend "
                f"could not draw"),
        }.get(self.state, "matplotlib is unavailable")

    def detail_lines(self) -> list[str]:
        """The block an operator needs to see the mismatch at a glance."""
        lines = [
            f"interpreter : {self.executable}",
            f"environment : {self.environment()}",
            f"matplotlib  : {self.version or 'not importable'}",
        ]
        if self.mplbackend:
            lines.append(f"MPLBACKEND  : {self.mplbackend}")
        if self.backend:
            lines.append(f"backend     : {self.backend}")
        lines.append(f"error       : {self.error}")
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "state": self.state,
            "error": self.error,
            "version": self.version,
            "backend": self.backend,
            "executable": self.executable,
            "environment": self.environment(),
            "mplbackend": self.mplbackend,
            "install_command": self.pip_command(),
            "summary": self.summary(),
        }


_cached: Optional[PlottingStatus] = None


def _probe() -> PlottingStatus:
    """Import matplotlib, pin the backend, and draw one throwaway figure."""
    # Pin the backend BEFORE matplotlib is imported: once pyplot has resolved a
    # backend, changing it is a switch that can itself fail. `setdefault` so an
    # operator who deliberately exports MPLBACKEND keeps their choice.
    os.environ.setdefault("MPLBACKEND", BACKEND)
    try:
        import matplotlib
    except ModuleNotFoundError as e:
        # The genuinely-missing case. Distinguished from every other
        # ImportError so the message can be specific about which is which.
        if (e.name or "").split(".")[0] == "matplotlib":
            return PlottingStatus(False, "not-installed",
                                  f"{type(e).__name__}: {e}")
        return PlottingStatus(False, "import-failed",
                              f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001 — a broken install is not "missing"
        return PlottingStatus(False, "import-failed",
                              f"{type(e).__name__}: {e}")

    version = getattr(matplotlib, "__version__", "?")
    try:
        # force=True: a switch that cannot happen must RAISE, not be ignored.
        # force=False was silently leaving an interactive backend in place on
        # headless machines and then failing at first figure creation.
        matplotlib.use(BACKEND, force=True)
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        return PlottingStatus(False, "backend-failed",
                              f"{type(e).__name__}: {e}", version=version)

    try:
        # A backend can import and still fail here (no display, missing font
        # cache, unwritable MPLCONFIGDIR). Better found at boot than mid-report.
        fig = plt.figure(figsize=(1, 1))
        plt.close(fig)
    except Exception as e:  # noqa: BLE001
        return PlottingStatus(False, "backend-failed",
                              f"{type(e).__name__}: {e}", version=version,
                              backend=str(matplotlib.get_backend()))

    return PlottingStatus(True, "ok", "", version=version,
                          backend=str(matplotlib.get_backend()))


def plotting_status(*, refresh: bool = False) -> PlottingStatus:
    """Cached result of the probe. Cheap to call from a request handler."""
    global _cached
    if _cached is None or refresh:
        _cached = _probe()
    return _cached


def require_plotting():
    """Return ``(plt, mdates)`` or raise :class:`PlottingUnavailable`."""
    status = plotting_status()
    if not status.available:
        raise PlottingUnavailable(status)
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    return plt, mdates


def unavailable_message(status: Optional[PlottingStatus] = None) -> str:
    """Plain-text diagnostic. Names the interpreter; quotes the real error."""
    st = status or plotting_status()
    body = "\n".join("  " + line for line in st.detail_lines())
    hint = (
        "The server is running under the interpreter above. Installing into a "
        "different Python will not change this. Install into THIS one:\n"
        f"  {st.pip_command()}"
        if st.state == "not-installed" else
        "matplotlib IS importable-or-installed for this interpreter, so this "
        "is not a missing package — the error above is the real failure."
    )
    return f"{st.headline()}.\n{body}\n{hint}"


def unavailable_html(status: Optional[PlottingStatus] = None) -> str:
    """The same diagnostic as an HTML fragment for a report section."""
    from html import escape

    st = status or plotting_status()
    lines = "<br>".join(escape(line) for line in st.detail_lines())
    if st.state == "not-installed":
        hint = ("The server is running under the interpreter above; installing "
                "into a different Python will not change this. Install into "
                f"THIS one: <code>{escape(st.pip_command())}</code>")
    else:
        hint = ("matplotlib is present for this interpreter, so this is "
                "<strong>not</strong> a missing package — the error above is "
                "the real failure.")
    return (f'<p class="note"><strong>Plot not rendered — '
            f'{escape(st.headline())}.</strong></p>'
            f'<pre class="note">{lines}</pre>'
            f'<p class="note">{hint}</p>')


def log_plotting_status(logger_: logging.Logger | None = None,
                        *, owner: str = "") -> PlottingStatus:
    """Log the reporting-extra status ONCE, at boot.

    A server that will not be able to draw a plot should say so when it starts,
    not forty minutes later inside a report an operator was waiting for.
    """
    log = logger_ or logger
    st = plotting_status()
    tag = f"{owner} " if owner else ""
    if st.available:
        log.info("%sreport plotting available: %s", tag, st.summary())
    else:
        log.warning("%sreport plotting UNAVAILABLE — plot sections will "
                    "degrade to a notice. %s", tag, st.summary())
        for line in st.detail_lines():
            log.warning("%s  %s", tag, line)
        if st.state == "not-installed":
            log.warning("%s  fix         : %s", tag, st.pip_command())
    return st
