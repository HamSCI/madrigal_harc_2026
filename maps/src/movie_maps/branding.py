"""Figure credit block.

The maps carry a *"Map by"* credit rather than a bare logo. The distinction is
deliberate and is argued in ``analysis/README.md``: HamSCI and the University of
Scranton made these maps, they did not deploy the network on them, and a logo
sitting alone in a corner reads as ownership or sponsorship.

Logos are listed in :data:`CREDIT_LOGOS` and any that are missing from disk are
skipped, so the figures still build on a checkout that lacks an asset.
"""

from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "assets"

#: (filename, width in inches), stacked top to bottom in the credit block.
#: Equal widths: the two wordmarks read as a set that way, and left-and-right
#: alignment matters more here than equalising their optical weight.
#: Sized so the stacked block matches the legend box it balances, in width and
#: roughly in height. Change it and the foot band resizes: callers derive their
#: geometry from credit_height().
CREDIT_WIDTH_IN = 2.05
CREDIT_LOGOS: tuple[tuple[str, float], ...] = (
    ("hamsci_logo_black.png", CREDIT_WIDTH_IN),
    ("scranton_logo.png", CREDIT_WIDTH_IN),
)


def available_logos() -> list[tuple[Path, float]]:
    """Return the credit logos present on disk, in display order."""
    return [(ASSETS / name, w) for name, w in CREDIT_LOGOS if (ASSETS / name).is_file()]


def credit_height(fig, label: bool = True, gap_in: float = 0.10) -> float:
    """Height of the credit block as a figure fraction.

    Callers size the foot band from this rather than guessing: the block's height
    follows from the logo width and the wordmarks' aspect ratios, so a change to
    :data:`CREDIT_WIDTH_IN` would otherwise silently push the logos into whatever
    sits above them.
    """
    import matplotlib.pyplot as plt

    logos = available_logos()
    if not logos:
        return 0.0
    fig_h = fig.get_size_inches()[1]
    total = 0.0
    for path, width_in in logos:
        img = plt.imread(str(path))
        total += width_in / (img.shape[1] / img.shape[0])
    total += gap_in * (len(logos) - 1)
    return total / fig_h + (0.024 if label else 0.0)


def add_credit(
    fig,
    label: str = "Map by",
    right: float = 0.955,
    bottom: float = 0.020,
    gap_in: float = 0.10,
    color: str = "#52514e",
    fontsize: float = 8.5,
) -> None:
    """Draw the credit block in the bottom-right corner of ``fig``.

    Bottom-**right** so it balances the legend in the bottom-left. Logos are
    right-aligned to a common edge and stacked upward from ``bottom``; the
    "Map by" label sits above the stack, aligned to the same edge.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
    label : str, optional
        Text above the logos. Set to ``""`` to omit it.
    right, bottom : float, optional
        Figure-fraction coordinates of the block's right and bottom edges.
    gap_in : float, optional
        Vertical gap between stacked logos, in inches.
    """
    import matplotlib.pyplot as plt

    logos = available_logos()
    if not logos:
        return
    fig_w, fig_h = fig.get_size_inches()
    gap = gap_in / fig_h

    y = bottom
    for path, width_in in reversed(logos):  # stack upward, first listed on top
        img = plt.imread(str(path))
        h = (width_in / (img.shape[1] / img.shape[0])) / fig_h
        w = width_in / fig_w
        ax = fig.add_axes((right - w, y, w, h), zorder=10)
        ax.imshow(img, interpolation="antialiased")
        ax.axis("off")
        ax.patch.set_alpha(0.0)
        y += h + gap

    if label:
        fig.text(right, y - gap + 0.004, label, fontsize=fontsize, color=color,
                 ha="right", va="bottom")
