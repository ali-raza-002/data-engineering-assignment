"""Calling windows, derived from where the called party actually is.

16 C.F.R. §310.4(c) and 47 C.F.R. §64.1200(c)(1) restrict outbound calls to
08:00-21:00 **local time at the called party's location**. Several states are
stricter — Florida §501.616(6) closes at 20:00, as do Oklahoma and Washington —
and the stricter window binds.

Two independent signals of "where they are", neither trustworthy alone:

  address   coordinates -> IANA zone. Accurate when we have an address.
  NPA-NXX   area code -> zone. Number portability and VoIP mean an area code is
            evidence of where a number was *issued*, not where it is answered.

When they disagree the **intersection** is taken, because being wrong in the
permissive direction is the expensive kind of wrong. Part 5c is explicit that a
pipeline computing a window from `SUBSTR(phone,1,3)` fails the section outright;
NPA here is a fallback and a cross-check, never the primary.

The NPA table covers the fixture and the three jurisdictions in scope. A
production system needs the full NANPA set — this is enough to exercise the
mechanism and the disagreement path honestly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.geo.timezone import TimezoneResolver

# Area code -> IANA zone. Deliberately partial; see module docstring.
# Sourced from NANPA assignments for MD / TX / FL plus the fixture's codes.
NPA_TO_ZONE: dict[str, str] = {
    # Maryland — entirely Eastern
    "301": "America/New_York", "240": "America/New_York",
    "410": "America/New_York", "443": "America/New_York", "667": "America/New_York",
    # Texas — Central, except the far west
    "214": "America/Chicago", "469": "America/Chicago", "972": "America/Chicago",
    "512": "America/Chicago", "737": "America/Chicago",
    "713": "America/Chicago", "281": "America/Chicago", "832": "America/Chicago",
    "210": "America/Chicago", "726": "America/Chicago",
    "817": "America/Chicago", "682": "America/Chicago",
    "915": "America/Denver",   # El Paso / Hudspeth — Mountain
    # Florida — Eastern, except the western Panhandle
    "305": "America/New_York", "786": "America/New_York",
    "407": "America/New_York", "321": "America/New_York",
    "813": "America/New_York", "727": "America/New_York",
    "904": "America/New_York", "561": "America/New_York", "954": "America/New_York",
    "850": "America/Chicago",  # Panhandle west of the Apalachicola — Central
}


@dataclass(frozen=True)
class CallingWindow:
    earliest: time
    latest: time
    basis: str
    citations: tuple[str, ...]
    sources_disagreed: bool = False

    def contains(self, local: time) -> bool:
        return self.earliest <= local <= self.latest

    @property
    def is_empty(self) -> bool:
        """True when narrowing left no lawful hour in both candidate zones."""
        return self.earliest >= self.latest


def npa_of(phone_e164: str | None) -> str | None:
    """Area code from an E.164 US number: +1 NPA NXX XXXX."""
    if not phone_e164:
        return None
    digits = re.sub(r"\D", "", phone_e164)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[:3] if len(digits) == 10 else None


def zone_from_npa(npa: str | None) -> str | None:
    """Area code -> zone. Takes the NPA, not a full number: the analytic record
    never holds one."""
    return NPA_TO_ZONE.get(npa) if npa else None


def _parse(hhmm: str) -> time:
    hour, minute = hhmm.split(":")
    return time(int(hour), int(minute))


def window_for(rules: dict, jurisdiction: str | None) -> tuple[time, time, list[str]]:
    """Federal window, narrowed by any state override."""
    cw = rules["calling_windows"]
    federal = cw["federal"]
    earliest, latest = _parse(federal["earliest"]), _parse(federal["latest"])
    citations = [federal["citation"]]

    override = cw.get("state_overrides", {}).get((jurisdiction or "").upper())
    if override:
        # The stricter of the two binds — never assume the override is stricter,
        # take the intersection so a laxer state rule cannot widen the federal one.
        earliest = max(earliest, _parse(override["earliest"]))
        latest = min(latest, _parse(override["latest"]))
        citations.append(override["citation"])

    return earliest, latest, citations


# Offsets are sampled in mid-winter and mid-summer because the two zones may
# not observe DST together (America/Phoenix does not), so their difference can
# change through the year. Narrowing by every observed difference is safe on
# any calendar date.
_OFFSET_SAMPLES = (datetime(2026, 1, 15, 12), datetime(2026, 7, 15, 12))


def _offset_hours(zone_a: str, zone_b: str) -> set[float]:
    """Hours to add to a wall-clock time in zone_a to get zone_b's."""
    a, b = ZoneInfo(zone_a), ZoneInfo(zone_b)
    return {
        (b.utcoffset(moment) - a.utcoffset(moment)) / timedelta(hours=1)
        for moment in _OFFSET_SAMPLES
    }


def _shift(t: time, hours: float) -> time:
    shifted = datetime.combine(datetime.min.date(), t) + timedelta(hours=hours)
    return shifted.time()


def _intersect(earliest: time, latest: time, home: str, other: str) -> tuple[time, time]:
    """Narrow [earliest, latest] in `home` local time so the call also lands
    inside [earliest, latest] in `other`. A party who might be an hour ahead
    loses an hour at the end; one who might be behind loses it at the start."""
    narrowed_from, narrowed_to = earliest, latest
    for delta in _offset_hours(home, other):
        if delta > 0:
            narrowed_to = min(narrowed_to, _shift(latest, -delta))
        elif delta < 0:
            narrowed_from = max(narrowed_from, _shift(earliest, -delta))
    return narrowed_from, narrowed_to


def resolve(
    rules: dict,
    *,
    jurisdiction: str | None,
    latitude: float | None,
    longitude: float | None,
    npa: str | None,
    resolver: TimezoneResolver,
) -> tuple[str | None, CallingWindow | None]:
    """Return (iana_zone, window). Zone is None when neither signal resolves —
    which is a blocking condition, not a default-to-Eastern."""
    address_zone = resolver.zone_for(latitude, longitude)
    npa_zone = zone_from_npa(npa)

    zone = address_zone or npa_zone
    if zone is None:
        return None, None

    earliest, latest, citations = window_for(rules, jurisdiction)
    disagreed = bool(address_zone and npa_zone and address_zone != npa_zone)

    if disagreed:
        # Neither signal is authoritative, so the window becomes the hours that
        # are lawful in BOTH zones, expressed in the address zone's local time.
        earliest, latest = _intersect(earliest, latest, address_zone, npa_zone)
        citations.append(
            "Number portability / VoIP: NPA-NXX is not reliable evidence of "
            "physical location, so the intersection of both candidate zones is used."
        )

    return zone, CallingWindow(
        earliest=earliest,
        latest=latest,
        basis="address_coordinates" if address_zone else "npa_nxx_fallback",
        citations=tuple(citations),
        sources_disagreed=disagreed,
    )
