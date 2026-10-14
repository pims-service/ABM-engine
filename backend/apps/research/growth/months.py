"""Calendar month arithmetic with end-of-month clamping."""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal


def shift_months(day: dt.date, months: int) -> dt.date:
    """Add (or, if negative, subtract) whole months, clamping the day to the month's length.

    ``2026-03-31`` minus one month is ``2026-02-28``; ``2024-03-31`` minus one is ``2024-02-29``.
    """
    index = day.year * 12 + (day.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day.day, last_day))


def months_between(start: dt.date, end: dt.date) -> Decimal:
    """Elapsed calendar months from ``start`` to ``end`` as a Decimal with 2 decimal places.

    Whole months by calendar arithmetic plus the leftover days as a fraction of the following
    month, so ``2025-10-13`` to ``2026-10-13`` is exactly ``12.00``.
    """
    whole = (end.year - start.year) * 12 + (end.month - start.month)
    if shift_months(start, whole) > end:
        whole -= 1
    anchor = shift_months(start, whole)
    following = shift_months(start, whole + 1)
    fraction = Decimal((end - anchor).days) / Decimal((following - anchor).days)
    return (Decimal(whole) + fraction).quantize(Decimal("0.01"))
