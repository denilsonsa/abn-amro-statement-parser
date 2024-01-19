from dataclasses import dataclass
from moneyed import Money


@dataclass(frozen=True)
class interval:
    """Defines a number interval.

    >>> 1.0 in range(0, 2)
    True
    >>> 1.0 in interval(0, 2)
    True
    >>> 1.5 in range(0, 2)
    False
    >>> 1.0 in interval(0, 2)
    True
    >>> 2.0 in interval(0, 2)
    False
    """
    low: float
    high: float

    def __contains__(self, n):
        return self.low <= n < self.high


def filter_comments(iterable):
    """Filters out comment lines and empty lines.

    This is useful when passing a file-like object as the iterable.

    >>> list(filter_comments([
    ...     "",
    ...     "first",
    ...     "",
    ...     "# foo",
    ...     "second",
    ...     "     # foo",
    ...     "last",
    ... ]))
    ['first', 'second', 'last']
    """
    for line in iterable:
        if line.lstrip().startswith("#"):
            continue
        if line.strip() == "":
            continue
        yield line


# Modified from https://stackoverflow.com/a/35513376
def first(iterable, condition=lambda x: True, default=None):
    """Returns the first item in the `iterable` that satisfies the `condition`.

    If the condition is not given, returns the first item of the iterable.

    >>> first((1,2,3), lambda x: x % 2 == 0)
    2
    >>> first(range(3, 100))
    3
    >>> first([]) is None
    True
    >>> first([1, 2, 3], lambda x: x > 5, -1)
    -1
    """
    return next((x for x in iterable if condition(x)), default)


def money_format(m: Money):
    """Given a moneyed.Money class, returns a sane and simple string representation.

    Rounding is arbitrary towards cents. I'm not working on any amount smaller
    than cents, so I'm not worrying about it.

    It doesn't respect locales, it always uses `.` as the decimal separator.
    The objective is to have a human-readable but also machine-readable money value.

    >>> money_format(Money("0.001", "EUR"))
    '0.00'
    >>> money_format(Money("0.012", "EUR"))
    '0.01'
    >>> money_format(Money("0.99", "EUR"))
    '0.99'
    >>> money_format(Money("1", "EUR"))
    '1.00'
    >>> money_format(Money("999", "EUR"))
    '999.00'
    >>> money_format(Money("1000", "EUR"))
    '1000.00'
    >>> money_format(Money("1234567.89", "EUR"))
    '1234567.89'
    """
    return "{:.2f}".format(m.amount)
