"""Optional visualization extra (matplotlib; install `minehaulsim[viz]`).

Import-guarded so the core stays numpy-only: importing this package without matplotlib raises
a clear error naming the extra, never a bare ImportError deep inside a render call.
"""
from __future__ import annotations

try:
    import matplotlib
    # This extra is a FILE renderer by design (SVG gallery artifacts, PNG contact sheets; no
    # show()). Agg keeps it working headless — CI runners and machines without a usable Tk.
    matplotlib.use("Agg")
    HAS_MPL = True
except ImportError:  # pragma: no cover - exercised only in minimal installs
    HAS_MPL = False


def require_mpl() -> None:
    if not HAS_MPL:
        raise ImportError(
            "minehaulsim.viz needs matplotlib — install the extra: pip install 'minehaulsim[viz]'")


from .planview import save_planview, plot_plan            # noqa: E402
from .profile import save_cycle_gantt, save_ramp_profile  # noqa: E402

__all__ = ["HAS_MPL", "require_mpl", "plot_plan", "save_planview",
           "save_ramp_profile", "save_cycle_gantt"]
