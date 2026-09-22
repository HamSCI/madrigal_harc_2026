"""Callsign normalisation, site grouping, and callsign-class heuristics.

Why this module exists
----------------------
A naive ``SELECT DISTINCT tx_sign`` over the study period returns **772,810**
callsigns from ``wsprdaemon.spots`` alone (measured 2026-07-28). The real WSPR
transmitter population is smaller by two orders of magnitude. The excess is
dominated by two things:

1. **Balloon-telemetry callsigns.** Protocols such as U4B encode telemetry in
   the callsign, grid and power fields, producing an endless stream of synthetic
   callsigns — ``Q51MXZ``, ``0I5WQZ``, ``128UAB``, ``065MIT``, ``177JWD`` — with
   *fabricated* grid squares and *fabricated* power values. Left in, they place
   thousands of phantom transmitters at random locations: an unfiltered census of
   June 2026 reports 32,757 "transmitters" above 70° absolute latitude, split
   almost evenly between hemispheres.
2. **False decodes.** The ``wsprd`` decoder occasionally emits a spurious
   callsign/grid/power triple. These appear once, at one receiver, on one day.

Every synthetic callsign observed in sampling begins with ``0``, ``1`` or ``Q``,
which is diagnostic: the ITU allocates no callsign prefix beginning with ``0`` or
``1``, and the ``Q`` block is reserved for Q-codes. That gives a cheap and
principled first filter.

It is not sufficient on its own, though, and the classifier here is deliberately
*secondary* to the persistence metrics computed in
:mod:`wspr_station_census.summarize`. Measured on ``wspr.rx`` for June 2026, the
callsign-shape filter alone cut 166,977 callsigns to 67,265 — still implausible —
whereas requiring activity on **three or more distinct days** cut 53,708 to
4,629. Persistence is the strong discriminator; callsign shape is corroboration.
"""

from __future__ import annotations

import re
from typing import Literal

CallsignClass = Literal["amateur", "synthetic_telemetry", "hashed", "nonstandard"]

#: A standard amateur callsign, without any portable prefix or suffix.
#:
#: Prefix forms admitted: one or two letters (``W``, ``DP``), a digit 2-9
#: followed by an optional letter (``9A``, ``4X``, ``2E``). Then a call-area
#: digit and a one-to-four letter suffix. ``Q`` is excluded from prefix
#: positions because the ITU reserves the ``Q`` block.
STANDARD_CALL = re.compile(r"^(?:[A-PR-Z]{1,2}|[2-9][A-PR-Z]?)[0-9]{1,2}[A-Z]{1,4}$")

#: Callsign shapes that cannot be an ITU allocation and are, in practice,
#: WSPR telemetry channels: a leading ``0`` or ``1``, a leading ``Q``, or two
#: leading digits.
SYNTHETIC_SHAPE = re.compile(r"^(?:[01]|Q|[2-9][0-9])")

#: Portable/receiver-instance suffix, e.g. ``/P``, ``/QRP``, ``/RS``, ``/OMNI``.
_SLASH_SUFFIX = re.compile(r"^[A-Z0-9]{1,5}$")

#: Trailing hyphenated receiver label, e.g. the ``-1`` of ``KD7EFG-1`` or the
#: ``-K1`` of ``W7WKR-K1``. ITU callsigns never contain a hyphen, so everything
#: from the first one onwards is an operator-added receiver label.
_SSID_SUFFIX = re.compile(r"-.*$")


def normalise(call: str | None) -> str:
    """Normalise a raw callsign field to upper case without hash brackets.

    Parameters
    ----------
    call : str or None
        Raw ``tx_sign`` / ``rx_sign`` value.

    Returns
    -------
    str
        Upper-cased, whitespace-stripped, with any surrounding ``<`` ``>``
        removed. Empty string for null input.

    Examples
    --------
    >>> normalise(" <vk6tq> ")
    'VK6TQ'
    >>> normalise("KD7EFG-1")
    'KD7EFG-1'
    """
    if not call:
        return ""
    return str(call).strip().upper().lstrip("<").rstrip(">")


def is_hashed(call: str | None) -> bool:
    """Return True if the raw field is a WSPR type-3 *hashed* callsign.

    Hashed callsigns appear in angle brackets (``<VK6TQ>``). They are real
    stations — the decoder resolved a 15-bit hash against calls it had seen
    recently — but the resolution can be wrong, so they are worth flagging
    rather than silently merging.
    """
    if not call:
        return False
    text = str(call)
    return "<" in text or ">" in text


def base_call(call: str | None) -> str:
    """Strip portable prefixes/suffixes to the underlying callsign.

    Among ``/``-separated segments, the one matching :data:`STANDARD_CALL` wins;
    if several match, the longest; if none match, the first. A trailing
    ``-N`` receiver index is then removed.

    This resolves the two conventions that both appear in the data: a *trailing*
    receiver or operating-status label (``HB9VQQ/RS``, ``OE3GBB/Q``,
    ``KFS/OMNI``, ``KD7EFG-1``) and a *leading* DXCC prefix (``KH6/W2NAF``).

    Parameters
    ----------
    call : str or None
        Raw or normalised callsign.

    Returns
    -------
    str
        The base callsign.

    Examples
    --------
    >>> base_call("HB9VQQ/RS")
    'HB9VQQ'
    >>> base_call("KH6/W2NAF")
    'W2NAF'
    >>> base_call("KD7EFG-1")
    'KD7EFG'
    >>> base_call("KFS/OMNI")
    'KFS'
    """
    text = normalise(call)
    if not text:
        return ""
    segments = [seg for seg in text.split("/") if seg]
    if not segments:
        return ""
    if len(segments) > 1:
        matching = [seg for seg in segments if STANDARD_CALL.match(_strip_ssid(seg))]
        if matching:
            chosen = max(matching, key=len)
        else:
            chosen = segments[0]
    else:
        chosen = segments[0]
    return _strip_ssid(chosen)


def _strip_ssid(text: str) -> str:
    """Remove a trailing ``-N`` receiver index."""
    return _SSID_SUFFIX.sub("", text)


def classify(call: str | None) -> CallsignClass:
    """Classify a callsign by shape.

    Parameters
    ----------
    call : str or None
        Raw callsign field, brackets included if present.

    Returns
    -------
    {'amateur', 'synthetic_telemetry', 'hashed', 'nonstandard'}
        ``'hashed'`` takes precedence, because the bracket notation is a fact
        about the message type rather than about the callsign. Otherwise
        ``'synthetic_telemetry'`` for shapes the ITU cannot allocate,
        ``'amateur'`` for shapes it can, and ``'nonstandard'`` for everything
        else — which includes genuine special-event and commercial calls without
        a call-area digit (``KFS``), so ``'nonstandard'`` is *not* a synonym for
        "bad".

    Examples
    --------
    >>> classify("W2NAF"), classify("DP0GVN"), classify("VY0ERC")
    ('amateur', 'amateur', 'amateur')
    >>> classify("Q51MXZ"), classify("0I5WQZ"), classify("128UAB")
    ('synthetic_telemetry', 'synthetic_telemetry', 'synthetic_telemetry')
    >>> classify("<VK6TQ>")
    'hashed'
    >>> classify("KFS")
    'nonstandard'
    """
    if is_hashed(call):
        return "hashed"
    text = normalise(call)
    if not text:
        return "nonstandard"
    stem = base_call(text)
    if SYNTHETIC_SHAPE.match(stem):
        return "synthetic_telemetry"
    if STANDARD_CALL.match(stem):
        return "amateur"
    return "nonstandard"


# ------------------------------------------------------------------- software

SoftwareClass = Literal["wsprdaemon", "kiwisdr", "web888", "wsjtx", "other", "unknown"]

#: Matches a bare WSJT-X style release number: ``2.7.0``, ``3.0.1``,
#: ``2.7.0-rc3``, ``2.2.159``.
_WSJTX_VERSION = re.compile(r"^[123]\.\d+(\.\d+)?(-rc\d+)?$")


def classify_software(version: str | None) -> SoftwareClass:
    """Classify reporting software from the ``wspr.rx`` ``version`` string.

    This is an independent line of evidence about a receiver, complementary to
    membership of ``wsprdaemon.spots``: a site can run WsprDaemon software
    without its extended spots reaching the table you are querying, and a site
    can run a standalone SDR that reports to wsprnet only.

    Note that ``version`` is **NULL for 95 of 100 receivers** in
    ``wsprdaemon.spots`` (measured June 2026), so this must be driven from
    ``wspr.rx``.

    Parameters
    ----------
    version : str or None
        Raw ``version`` value.

    Returns
    -------
    {'wsprdaemon', 'kiwisdr', 'web888', 'wsjtx', 'other', 'unknown'}
        ``'other'`` means *recognised but unclassified* reporting software, not
        junk; the raw strings are preserved in the ``versions`` output column so
        the mapping can be refined without re-querying.

    Examples
    --------
    >>> classify_software("WD_3.3.2-2")
    'wsprdaemon'
    >>> classify_software("1.4A Kiwi")
    'kiwisdr'
    >>> classify_software("web-888")
    'web888'
    >>> classify_software("2.7.0")
    'wsjtx'
    >>> classify_software("v1.2.117")
    'other'
    >>> classify_software(None)
    'unknown'
    """
    if version is None:
        return "unknown"
    text = str(version).strip()
    if not text or text in {"\\N", "None", "nan"}:
        return "unknown"
    upper = text.upper()
    if upper.startswith("WD_") or upper.startswith("WD-") or "WSPRDAEMON" in upper:
        return "wsprdaemon"
    if "KIWI" in upper:
        return "kiwisdr"
    if "WEB-888" in upper or "WEB888" in upper:
        return "web888"
    if _WSJTX_VERSION.match(text):
        return "wsjtx"
    return "other"
