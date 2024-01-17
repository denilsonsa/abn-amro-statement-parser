#!/bin/env python3

import csv
import os.path
import re
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, date
from moneyed import Currency, Money

try:
    from itertools import batched
except ImportError:
    from itertools import islice

    # It was introduced in Python 3.12
    def batched(iterable, n):
        """Returns tuples of batched items from the iterable.

        It was introduced in Python 3.12.
        This code here just replicates the behavior for older versions.

        >>> list(batched("ABCDEFG", 3))
        [('A', 'B', 'C'), ('D', 'E', 'F'), ('G',)]
        """
        if n < 1:
            raise ValueError("n must be at least one")
        it = iter(iterable)
        while batch := tuple(islice(it, n)):
            yield batch


# The column order in the XLS is:
# HEADERS = [
#     "accountNumber",
#     "mutationcode",
#     "transactiondate",
#     "valuedate",  # <- This moves to a different column in the TSV.
#     "startsaldo",
#     "endsaldo",
#     "amount",
#     "description",  # <- This is annoying to parse
# ]

# The files named like `TXT231122235959.TAB` are TSV (tab-separated values).
# The column order in the TSV is:
HEADERS = [
    "accountNumber",
    "mutationcode",
    "transactiondate",
    "startsaldo",
    "endsaldo",
    "valuedate",
    "amount",
    "description",  # <- This is annoying to parse
]

# This named tuple contains the raw data directly read from the TSV file.
RowTuple = namedtuple("RowTuple", HEADERS)


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


@dataclass
class Transaction:
    """Dataclass for each of the transactions of a TSV file.

    Each row from the TSV file is a transaction.
    Each transaction can be represented by this class.
    This class includes a few convenience methods and properties.
    """

    # The account number.
    account: int

    # The date of the transaction.
    # There is also the "value date" which is ignored here because it is rarely
    # different than the "transaction date".
    date: date
    # An arbitrary incrementing number to keep the order of the transactions
    # from the same day. It can be initialized as the row number from the
    # imported file.
    order: int

    # Three-letter code.
    currency: Currency

    amount: Money
    start_saldo: Money
    end_saldo: Money

    description: str

    def __post_init__(self):
        self._desc_str = None
        self._desc = None

    def __eq__(self, other):
        """Compares if two rows are the same, ignoring unreliable fields.

        Assumes that two rows are the same if these columns are the same:

        * account
        * date
        * currency
        * amount
        * start_saldo
        * end_saldo

        It explicitly ignores these fields:

        * order (this is an arbitrary number not part of the original file)
        * description (the same transaction has different description text depending on when it was downloaded)

        >>> sample_data = {
        ...     "account": [1234, 5678],
        ...     "date": [date(2024, 1, 1), date(2024, 1, 2)],
        ...     "order": [1, 2],
        ...     "currency": [Currency("EUR"), Currency("USD")],
        ...     "amount": [Money("-12.34", "EUR"), Money("12.34", "USD")],
        ...     "start_saldo": [Money("112.34", "EUR"), Money("100.00", "USD")],
        ...     "end_saldo": [Money("100.00", "EUR"), Money("112.34", "USD")],
        ...     "description": ["SEPA whatever", "/TRTP/SEPA whatever/"],
        ... }
        >>> build_row = lambda changekey: Transaction(**{ k: v[0 if k != changekey else 1] for (k,v) in sample_data.items() })
        >>> a = build_row("")
        >>> a != build_row("account")
        True
        >>> a != build_row("date")
        True
        >>> a != build_row("currency")
        True
        >>> a != build_row("amount")
        True
        >>> a != build_row("start_saldo")
        True
        >>> a != build_row("end_saldo")
        True
        >>> a == build_row("order")
        True
        >>> a == build_row("description")
        True
        """
        return (
            self.account == other.account
            and self.date == other.date
            and self.currency == other.currency
            and self.amount == other.amount
            and self.start_saldo == other.start_saldo
            and self.end_saldo == other.end_saldo
        )

    @property
    def amount_formatted(self):
        """The amount of money, formatted in a simple way."""
        return money_format(self.amount)

    @property
    def desc(self):
        """Dict object with the parsed description.

        The raw description string is a mess. Many different formats, many
        fields in a single string with no easy delimiter, a lot of extra
        machine-readable codes... It is very hard to extract useful information
        from it.

        This property returns a nice dict object with all the known fields
        properly parsed and extracted from the raw string, with any trailing
        whitespace removed.
        """
        if self._desc_str is self.description:
            # Return the cached dict.
            return self._desc
        else:
            # Update the cached dict.
            self._desc_str = self.description
            self._desc = parse_description(self.description)
            return self._desc

    @property
    def as_json_like(self):
        """Returns a JSON-serializable object.

        Returns a dict containing only data types that can be serialized as JSON:
        lists, dicts, strings, numbers, booleans.
        """
        return {
            "account": self.account,
            "date": self.date.isoformat(),
            "order": self.order,
            "currency": self.currency.code,
            "amount": money_format(self.amount),
            "start_saldo": money_format(self.start_saldo),
            "end_saldo": money_format(self.end_saldo),
            "description": self.description,  # raw description string
            "desc": self.desc,  # parsed description as a dict
        }


def rejoin_description(s):
    """Removes the extraneous space characters inserted about every 64 chars.

    For whatever legacy or stupid reason, the description field contains
    extraneous space characters every about 64 chars. It looks like a legacy
    system would print the description in a 64-column multi-line format, and
    then another system would replace the newlines with extra spaces.
    Unfortunately, this ends up inserting spaces in the middle of words, which
    is not desirable.

    The description composed of several slashes don't include any additional
    space character, and is returned as is:

    >>> example = [
    ...     "SEPA iDEAL                      ",
    ...     "IBAN: NL01RABO0123456789        BIC: RABONL2U                   ",
    ...     "Naam: Next to Pay via Mollie    Omschrijving: M01234567ABCDE0F 0",
    ...     "123456789012345 Foobar Pizza Delivery Order 123456              ",
    ...     "Kenmerk: 31-12-2023 17:01 0123456789012345                      ",
    ...     "                                ",
    ... ]
    >>> "".join(example).rstrip() == rejoin_description(" ".join(example))
    True

    >>> slashes = "".join([
    ...     "/TRTP/SEPA Incasso algemeen doorlopend",
    ...     "/CSID/NL00ZZZ123456789012",
    ...     "/NAME/Albert Heijn B.V.",
    ...     "/MARF/AH012345678901234567890123456789012",
    ...     "/REMI/Foobarment Foobar Fobar - AB012345678",
    ...     "/IBAN/NL00INGB0123456789",
    ...     "/BIC/INGBNL2A",
    ...     "/EREF/AB0123456789",
    ... ])
    >>> slashes == rejoin_description(slashes)
    True
    """
    if s.startswith("/"):
        return s
    else:
        # Spaces are inserted every 32 or 64 characters.
        # It's annoying.
        head = s[:32]
        assert s[32] == " "
        parts = re.findall(r".{1,64} ?", s[33:].rstrip())
        assert all(len(p) == 65 for p in parts[:-1])
        return "".join([head, *[p[:64] for p in parts]])


def parse_nr_datetime(s):
    """Given a datetime string from the bank, returns a proper datetime object.

    The bank changed the time format at some point, so sometimes it uses `.`
    and other times it uses `:`. As bonus, this function also supports `-`.

    >>> parse_nr_datetime("31.12.23/23.59")
    datetime.datetime(2023, 12, 31, 23, 59)
    >>> parse_nr_datetime("31.12.23/23:59")
    datetime.datetime(2023, 12, 31, 23, 59)
    """
    parts = re.split("[-:./]", s.strip())
    dd, mm, yy, HH, MM = [int(p) for p in parts]
    return datetime(2000 + yy, mm, dd, HH, MM)


def parse_description(s):
    r"""Returns a dict of the description, parsed into many fields.

    There are so many formats for the description...
    This code tries to support them all.
    And also tries to add a doctest to each of them.
    It works a both documentation and as a test. (It's a doctest after all!)
    Of course, numbers and names were anonymized.

    >>> import json
    >>> test_it = lambda s: print(json.dumps(parse_description(rejoin_description(s)), indent=2, sort_keys=True))

    This is the slash-separated item. Only for SEPA, only for some of the
    "recent enough" transactions. All the codenames can be mapped to more
    human-readable names.

    >>> test_it("".join([
    ...     "/TRTP/SEPA Incasso algemeen doorlopend",
    ...     "/CSID/NL00ZZZ123456789012",
    ...     "/NAME/Albert Heijn B.V.",
    ...     "/MARF/AH012345678901234567890123456789012",
    ...     "/REMI/Foobarment Foobar Fobar - AB012345678",
    ...     "/IBAN/NL00INGB0123456789",
    ...     "/BIC/INGBNL2A",
    ...     "/EREF/AB0123456789",
    ... ]))
    {
      "BIC": "INGBNL2A",
      "IBAN": "NL00INGB0123456789",
      "Incassant": "NL00ZZZ123456789012",
      "Kenmerk": "AB0123456789",
      "Machtiging": "AH012345678901234567890123456789012",
      "Naam": "Albert Heijn B.V.",
      "Omschrijving": "Foobarment Foobar Fobar - AB012345678",
      "type": "SEPA Incasso algemeen doorlopend"
    }

    A few fields are excluded because they are useless, and because they don't
    show up in the normal plaintext version without slashes.

    >>> test_it("".join([
    ...     "/TRTP/SEPA OVERBOEKING",
    ...     "/IBAN/NL01INGB0123456789",
    ...     "/BIC/INGBNL2A",
    ...     "/NAME/Foobar-Fizzbuzz",
    ...     "/REMI/EXCNR: 012345678 AB 1.234,56 CD 1.234,56 EFG 12,34. Na het einde van de maand vind je de specificatie op foo.bar.nl",
    ...     "/EREF/012345678901",
    ...     "/ORDP/",
    ...     "/ID/99999999               ",
    ... ]))
    {
      "BIC": "INGBNL2A",
      "IBAN": "NL01INGB0123456789",
      "Kenmerk": "012345678901",
      "Naam": "Foobar-Fizzbuzz",
      "Omschrijving": "EXCNR: 012345678 AB 1.234,56 CD 1.234,56 EFG 12,34. Na het einde van de maand vind je de specificatie op foo.bar.nl",
      "type": "SEPA OVERBOEKING"
    }

    For consistency, we replace `iDEAL` with `SEPA iDEAL`.

    >>> test_it("".join([
    ...     "/TRTP/iDEAL",
    ...     "/IBAN/NL01ABNA0123456789",
    ...     "/BIC/ABNANL2A",
    ...     "/NAME/Tikkie Zakelijk",
    ...     "/REMI/B20230101X00ABCD012345678901 0123456789012345 Fizzbuzz Foo Bar NL02ABNA1234567890 Tikkie Zakelijk",
    ...     "/EREF/01-01-2023 13:37 0123456789012345                                               ",
    ... ]))
    {
      "BIC": "ABNANL2A",
      "IBAN": "NL01ABNA0123456789",
      "Kenmerk": "01-01-2023 13:37 0123456789012345",
      "Naam": "Tikkie Zakelijk",
      "Omschrijving": "B20230101X00ABCD012345678901 0123456789012345 Fizzbuzz Foo Bar NL02ABNA1234567890 Tikkie Zakelijk",
      "type": "SEPA iDEAL"
    }

    Everything that is not slash-separated has extra spaces added every 32 or
    64 characters.

    The transactions for the bank fees have a unique format.
    Notice how we are replacing the decimal separator.

    >>> test_it(" ".join([
    ...     "ABN AMRO Bank N.V.              ",
    ...     "Credit Card                 1,70CreditCard(2)               1,00",
    ...     "Basic Package               1,70Debit card                  1,40",
    ...     "Debit card                  1,40",
    ... ]))
    {
      "Basic Package": "1.70",
      "Credit Card": "1.70",
      "CreditCard(2)": "1.00",
      "Debit card": "1.40",
      "type": "ABN AMRO Bank N.V."
    }
    >>> test_it(" ".join([
    ...     "ABN AMRO Bank N.V.              ",
    ...     "CreditCard                  1,70Cr.CardExtra                1,00",
    ...     "Basic Package               2,95Debit card                  1,40",
    ...     "                                ",
    ... ]))
    {
      "Basic Package": "2.95",
      "Cr.CardExtra": "1.00",
      "CreditCard": "1.70",
      "Debit card": "1.40",
      "type": "ABN AMRO Bank N.V."
    }

    The interest on the savings account also has a unique format. But it is so
    simple and so rare (once a year) that it is not worth parsing any further.

    >>> test_it(" ".join([
    ...     "Basic interest                  ",
    ...     "over the period from            31-12-2022 to 31-12-2023        ",
    ...     "For interest rates please visit www.abnamro.nl/rente            ",
    ...     "                                ",
    ... ]))
    {
      "description": "over the period from 31-12-2022 to 31-12-2023 For interest rates please visit www.abnamro.nl/rente",
      "type": "Basic interest"
    }
    >>> test_it(" ".join([
    ...     "CREDIT INTEREST                 ",
    ...     "                                ",
    ... ]))
    {
      "description": "",
      "type": "Basic interest"
    }

    Likewise for the only three unique transactions regarding insurance.
    This format was rare, and doesn't show up anymore.

    >>> test_it(" ".join([
    ...     "Maandpremie juni 2021           ",
    ...     "van verzekering 123456789       ",
    ... ]))
    {
      "description": "Maandpremie juni 2021 van verzekering 123456789",
      "type": "legacy insurance"
    }
    >>> test_it(" ".join([
    ...     "Uitbetaling pakketkorting       ",
    ...     "van verzekering 123456789       ",
    ... ]))
    {
      "description": "Uitbetaling pakketkorting van verzekering 123456789",
      "type": "legacy insurance"
    }
    >>> test_it(" ".join([
    ...     "Uitbetaling pakketkorting       ",
    ...     "van verzekering 123456789       ",
    ... ]))
    {
      "description": "Uitbetaling pakketkorting van verzekering 123456789",
      "type": "legacy insurance"
    }
    >>> test_it(" ".join([
    ...     "PAKKETVERZ. POLISNR.   123456789",
    ...     "MAANDPREMIE 02-17               ",
    ... ]))
    {
      "description": "PAKKETVERZ. POLISNR. 123456789 MAANDPREMIE 02-17",
      "type": "legacy insurance"
    }
    >>> test_it(" ".join([
    ...     "PAKKETVERZ. POLISNR.   123456789",
    ...     "VERZEKERINGSBEWIJS DD 13-02-17  ",
    ... ]))
    {
      "description": "PAKKETVERZ. POLISNR. 123456789 VERZEKERINGSBEWIJS DD 13-02-17",
      "type": "legacy insurance"
    }

    SEPA (Single Euro Payments Area) is for (online) bank transfers.
    They can be single payments over iDEAL (mostly for online purchases),
    or simple bank transfers, or subscription payments.

    >>> test_it(" ".join([
    ...     "SEPA iDEAL                      ",
    ...     "IBAN: NL01RABO0123456789        BIC: RABONL2U                   ",
    ...     "Naam: Next to Pay via Mollie    Omschrijving: M01234567ABCDE0F 0",
    ...     "123456789012345 Foobar Pizza Delivery Order 123456              ",
    ...     "Kenmerk: 31-12-2023 17:01 0123456789012345                      ",
    ...     "                                ",
    ... ]))
    {
      "BIC": "RABONL2U",
      "IBAN": "NL01RABO0123456789",
      "Kenmerk": "31-12-2023 17:01 0123456789012345",
      "Naam": "Next to Pay via Mollie",
      "Omschrijving": "M01234567ABCDE0F 0123456789012345 Foobar Pizza Delivery Order 123456",
      "type": "SEPA iDEAL"
    }
    >>> test_it(" ".join([
    ...     "SEPA Incasso algemeen doorlopend",
    ...     "Incassant: NL01ZZZ012345678901  Naam: FOO BAR FIZZ BUZZ FOOBAR  ",
    ...     "Machtiging: 012345678901        Omschrijving: Factuur: 012345678",
    ...     "901                             IBAN: NL01ABNA0123456789        ",
    ...     "Kenmerk: 012345678901           Voor: J SMITH VAN DE FOOBAR CJ  ",
    ...     "                                 ",
    ... ]))
    {
      "IBAN": "NL01ABNA0123456789",
      "Incassant": "NL01ZZZ012345678901",
      "Kenmerk": "012345678901",
      "Machtiging": "012345678901",
      "Naam": "FOO BAR FIZZ BUZZ FOOBAR",
      "Omschrijving": "Factuur: 012345678901",
      "Voor": "J SMITH VAN DE FOOBAR CJ",
      "type": "SEPA Incasso algemeen doorlopend"
    }
    >>> test_it(" ".join([
    ...     "SEPA Incasso algemeen doorlopend",
    ...     "Incassant: GB98NFXSDDCHAS01234567890123                         ",
    ...     "Naam: NETFLIX INTERNATIONAL B.V.Machtiging: DD-01234567890123456",
    ...     "7-890-123456                    Omschrijving: Netflix Monthly Su",
    ...     "bscription                      IBAN: LU012345678901234567      ",
    ...     "                                ",
    ... ]))
    {
      "IBAN": "LU012345678901234567",
      "Incassant": "GB98NFXSDDCHAS01234567890123",
      "Machtiging": "DD-012345678901234567-890-123456",
      "Naam": "NETFLIX INTERNATIONAL B.V.",
      "Omschrijving": "Netflix Monthly Subscription",
      "type": "SEPA Incasso algemeen doorlopend"
    }
    >>> test_it(" ".join([
    ...     "SEPA Incasso algemeen eenmalig  ",
    ...     "Incassant: NL01ZZZ012345678901  Naam: Association Foobar fiz BUZ",
    ...     "Z by Fobar                      Machtiging: A0B1C2D3E4F5G6H7    ",
    ...     "Omschrijving: Association Foobar fiz BUZZ 01234567 89ab cdef 012",
    ...     "3 456789abcdef:01234567 89ab cdef 0123 456789abcdef             ",
    ...     "                                ",
    ... ]))
    {
      "Incassant": "NL01ZZZ012345678901",
      "Machtiging": "A0B1C2D3E4F5G6H7",
      "Naam": "Association Foobar fiz BUZZ by Fobar",
      "Omschrijving": "Association Foobar fiz BUZZ 01234567 89ab cdef 0123 456789abcdef:01234567 89ab cdef 0123 456789abcdef",
      "type": "SEPA Incasso algemeen eenmalig"
    }
    >>> test_it(" ".join([
    ...     "SEPA Overboeking                ",
    ...     "IBAN: NL01ABNA0123456789        BIC: ABNANL2A                   ",
    ...     "Naam: J SMITH VAN DE FOOBAR CJ  ",
    ... ]))
    {
      "BIC": "ABNANL2A",
      "IBAN": "NL01ABNA0123456789",
      "Naam": "J SMITH VAN DE FOOBAR CJ",
      "type": "SEPA Overboeking"
    }
    >>> test_it(" ".join([
    ...     "SEPA Overboeking                ",
    ...     "IBAN: NL01RABO0123456789        BIC: RABONL2U                   ",
    ...     "Naam: Praktijk Foobar           Omschrijving: nota nr 0123456789",
    ...     "0 - Fizzbuzz                    ",
    ... ]))
    {
      "BIC": "RABONL2U",
      "IBAN": "NL01RABO0123456789",
      "Naam": "Praktijk Foobar",
      "Omschrijving": "nota nr 01234567890 - Fizzbuzz",
      "type": "SEPA Overboeking"
    }
    >>> test_it(" ".join([
    ...     "SEPA Overboeking                ",
    ...     "IBAN: NL01INGB2345678901        BIC: INGBNL2A                   ",
    ...     "Naam: FOO                       Omschrijving: P00001000000000001",
    ...     "23456789012345 FOO/BAR 01-01-23/31-01-23 Foobar                 ",
    ...     "Kenmerk: AB01 234567CD-01234567890                              ",
    ...     "                                ",
    ... ]))
    {
      "BIC": "INGBNL2A",
      "IBAN": "NL01INGB2345678901",
      "Kenmerk": "AB01 234567CD-01234567890",
      "Naam": "FOO",
      "Omschrijving": "P0000100000000000123456789012345 FOO/BAR 01-01-23/31-01-23 Foobar",
      "type": "SEPA Overboeking"
    }

    GEA is for ATM machines (geldmaat).
    BEA is for physical payments.
    Newer BEA transactions specify the kind of device used for the payment.
    They both have mostly the same format.

    The store name seems to be cropped around 22 or 24 characters.
    The location seems to be cropped to 13 characters.

    >>> test_it(" ".join([
    ...     "BEA   NR:AB012345 31.12.21/12.34",
    ...     "CCV TRAVERSE P1,PAS123          LUCHTH SCHIPH",
    ... ]))
    {
      "NR": "AB012345",
      "Naam": "CCV TRAVERSE P1",
      "card": "123",
      "datetime": "2021-12-31T12:34:00",
      "location": "LUCHTH SCHIPH",
      "suffix": "",
      "type": "BEA"
    }
    >>> test_it(" ".join([
    ...     "BEA   NR:A1B23C   31.12.21/01.02",
    ...     "Hema EV123,PAS456               ZAANDAM                         ",
    ...     "TERUGBOEKING-BEA-TRANSACTIE",
    ... ]))
    {
      "NR": "A1B23C",
      "Naam": "Hema EV123",
      "card": "456",
      "datetime": "2021-12-31T01:02:00",
      "location": "ZAANDAM",
      "suffix": "TERUGBOEKING-BEA-TRANSACTIE",
      "type": "BEA"
    }

    >>> test_it(" ".join([
    ...     "GEA, Betaalpas                  ",
    ...     "Geldmaat Somewhere 22,PAS456    NR:012345, 25.12.23/12:21       ",
    ...     "Somewhere                       ",
    ... ]))
    {
      "NR": "012345",
      "Naam": "Geldmaat Somewhere 22",
      "card": "456",
      "datetime": "2023-12-25T12:21:00",
      "location": "Somewhere",
      "suffix": "",
      "type": "GEA, Betaalpas"
    }

    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "IKEA Amsterdam,PAS123           NR:0ABC0D, 01.02.23/14:15       ",
    ...     "AMSTERDAM                       TERUGBOEKING BEA-TRANSACTIE     ",
    ...     "                                ",
    ... ]))
    {
      "NR": "0ABC0D",
      "Naam": "IKEA Amsterdam",
      "card": "123",
      "datetime": "2023-02-01T14:15:00",
      "location": "AMSTERDAM",
      "suffix": "TERUGBOEKING BEA-TRANSACTIE",
      "type": "BEA, Betaalpas"
    }
    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "Zettle_*The Whatever S,PAS123   NR:01234567, 03.04.23/14:25     ",
    ...     "Eindhoven, No                   ",
    ... ]))
    {
      "NR": "01234567",
      "Naam": "Zettle_*The Whatever S",
      "card": "123",
      "datetime": "2023-04-03T14:25:00",
      "location": "Eindhoven, No",
      "suffix": "",
      "type": "BEA, Betaalpas"
    }
    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "TUIFLY NL,PAS123                NR:01234567, 21.12.23/12:21     ",
    ...     "SCHIPHOL RIJK, Land: IRL        ",
    ... ]))
    {
      "NR": "01234567",
      "Naam": "TUIFLY NL",
      "card": "123",
      "datetime": "2023-12-21T12:21:00",
      "location": "SCHIPHOL RIJK, Land: IRL",
      "suffix": "",
      "type": "BEA, Betaalpas"
    }
    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "AB CDEFGHIJ,PAS123              NR:76543210, 05.04.23/06:07     ",
    ...     "KLEOPATRAS 5, Land: GRC         ",
    ... ]))
    {
      "NR": "76543210",
      "Naam": "AB CDEFGHIJ",
      "card": "123",
      "datetime": "2023-04-05T06:07:00",
      "location": "KLEOPATRAS 5, Land: GRC",
      "suffix": "",
      "type": "BEA, Betaalpas"
    }
    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "SSP FOOBAR BURGER KI,PAS456     NR:76543210 22.01.23/22.23      ",
    ...     "KRATIKOS AERO,Land: GR          NAGEKOMEN VERREKENING           ",
    ...     "                                ",
    ... ]))
    {
      "NR": "76543210",
      "Naam": "SSP FOOBAR BURGER KI",
      "card": "456",
      "datetime": "2023-01-22T22:23:00",
      "location": "KRATIKOS AERO,Land: GR",
      "suffix": "NAGEKOMEN VERREKENING",
      "type": "BEA, Betaalpas"
    }

    >>> test_it(" ".join([
    ...     "BEA, Google Pay                 ",
    ...     "NLOVAB1C2D3E4F5G6H,PAS123       NR:AB12CD34, 11.12.23/11:12     ",
    ...     "www.ovpay.nl                    ",
    ... ]))
    {
      "NR": "AB12CD34",
      "Naam": "NLOVAB1C2D3E4F5G6H",
      "card": "123",
      "datetime": "2023-12-11T11:12:00",
      "location": "www.ovpay.nl",
      "suffix": "",
      "type": "BEA, Google Pay"
    }
    >>> test_it(" ".join([
    ...     "BEA, Google Pay                 ",
    ...     "SumUp  *European Fooba,PAS123   NR:12345678, 30.03.23/03:30     ",
    ...     "Gateshead, Land: GBR            GBP 10,00 1EUR=0,8539 GBP       ",
    ...     "KOSTEN EUR0,15 ACHTERAF BEREKEND",
    ... ]))
    {
      "NR": "12345678",
      "Naam": "SumUp  *European Fooba",
      "card": "123",
      "datetime": "2023-03-30T03:30:00",
      "location": "Gateshead, Land: GBR",
      "suffix": "GBP 10,00 1EUR=0,8539 GBP       KOSTEN EUR0,15 ACHTERAF BEREKEND",
      "type": "BEA, Google Pay"
    }
    >>> test_it(" ".join([
    ...     "BEA, Google Pay                 ",
    ...     "PiPi H'dorp De Brug Li,PAS123   NR:AB123C, 20.02.23/20:02       ",
    ...     "HOOFDDORP                       ",
    ... ]))
    {
      "NR": "AB123C",
      "Naam": "PiPi H'dorp De Brug Li",
      "card": "123",
      "datetime": "2023-02-20T20:02:00",
      "location": "HOOFDDORP",
      "suffix": "",
      "type": "BEA, Google Pay"
    }

    This is a snowflake, one-of-a-kind transaction.
    It's the only transaction I found that had backslashes.

    >>> test_it(" ".join([
    ...     "BEA, Betaalpas                  ",
    ...     "WS Wormerveer\\Wandelwe,PAS123   NR:01234567 22.02.23/02.22      ",
    ...     " 96\\Wormervee                   TERUGBOEKING-BEA-TRANSACTIE     ",
    ...     "                                ",
    ... ]))
    {
      "NR": "01234567",
      "Naam": "WS Wormerveer\\Wandelwe",
      "card": "123",
      "datetime": "2023-02-22T02:22:00",
      "location": " 96\\Wormervee",
      "suffix": "TERUGBOEKING-BEA-TRANSACTIE",
      "type": "BEA, Betaalpas"
    }

    """
    if s.startswith("/"):
        parts = []
        for p in s[1:].split("/"):
            if len(parts) % 2 == 0:
                if re.fullmatch(r"TRTP|CSID|NAME|REMI|MARF|EREF|IBAN|BIC|ORDP|ID", p):
                    parts.append(p)
                else:
                    parts.append(parts.pop() + "/" + p)
            else:
                parts.append(p)
        key_map = {
            "TRTP": "type",
            "CSID": "Incassant",
            "NAME": "Naam",
            "REMI": "Omschrijving",
            "MARF": "Machtiging",
            "EREF": "Kenmerk",
            "IBAN": "IBAN",
            "BIC": "BIC",
            "ORDP": "",
            "ID": "",
        }
        data = dict(
            (key_map[k], v.rstrip()) for (k, v) in batched(parts, 2) if key_map[k] != ""
        )
        if data["type"] == "iDEAL":
            # To make it consistent with the other format.
            data["type"] = "SEPA iDEAL"
        return {
            **data,
        }
    else:
        head = s[:32].rstrip()
        tail = s[32:].rstrip()
        if s.startswith("ABN AMRO Bank"):
            # Bank fees.
            parts = re.findall(r".{1,32}", tail)
            costs = dict(
                (k, v.replace(",", "."))
                for (k, v) in (
                    re.fullmatch(r"^(.*[^ ]) +([-0-9,.]+)$", p).groups() for p in parts
                )
            )
            return {
                "type": head,
                **costs,
            }
        elif re.match(r"^BEA ", head):
            # Legacy, old format for in-person payments.
            name_and_card = tail[0:32]
            location = tail[32:64]
            suffix = tail[64:]

            type, nr, dtstr = re.fullmatch(
                r"^(BEA) +NR:([^ ]+) +([0-9./:]+)$", head
            ).groups()
            name, _, pas = name_and_card.partition(",PAS")
            dt = parse_nr_datetime(dtstr)
            return {
                "type": type,
                "datetime": dt.isoformat(),
                "NR": nr,
                "Naam": name.rstrip(),
                "card": pas.rstrip(),
                "location": location.rstrip(),
                "suffix": suffix.rstrip(),
            }

        elif re.match(r"^(BEA|GEA), ", head):
            # Newer format for payments and ATM.
            name_and_card = tail[0:32]
            nr_and_date = tail[32:64]
            location = tail[64:96]
            suffix = tail[96:]

            name, _, pas = name_and_card.partition(",PAS")
            nr, dtstr = re.fullmatch(
                r"^NR:([^, ]+)[, ]+([0-9./:]+) *", nr_and_date
            ).groups()
            dt = parse_nr_datetime(dtstr)

            return {
                "type": head,
                "datetime": dt.isoformat(),
                "NR": nr,
                "Naam": name.rstrip(),
                "card": pas.rstrip(),
                "location": location.rstrip(),
                "suffix": suffix.rstrip(),
            }
        elif re.match(r"^SEPA ", head):
            # Online transactions.
            parts = []
            for thirtytwo in re.findall(r".{1,32}", tail):
                # Human-readable:
                #     Naam, Omschrijving
                # Readable, but mostly useless:
                #     Voor
                # Codes for machines:
                #     Incassant, BIC, Machtiging, IBAN, Kenmerk
                if match := re.fullmatch(
                    r"^(Incassant|BIC|Naam|Machtiging|Omschrijving|IBAN|Kenmerk|Voor): (.+)",
                    thirtytwo,
                    # Note: It may be worth adding the `re.I` flag if using
                    # this function against the description from MT940 files,
                    # as those are ALL CAPS.
                ):
                    parts.append((match.group(1), match.group(2)))
                else:
                    key, value = parts.pop()
                    parts.append((key, value + thirtytwo))
            return {
                "type": head,
                **{k: v.strip() for (k, v) in parts},
            }
        elif re.match(r"^CREDIT INTEREST", head):
            # Legacy, old format for savings account interest.
            return {
                "type": "Basic interest",
                "description": tail,  # Empty in this case.
            }
        elif re.match(r"^Basic interest", head):
            # Newer format for savings account interest.
            return {
                "type": head,
                "description": re.sub(r" +", " ", tail),
            }
        elif re.match(
            r"^(Maandpremie |Uitbetaling pakketkorting|PAKKETVERZ\. POLISNR\.)", head
        ):
            # Legacy, old format for insurance costs.
            return {
                "type": "legacy insurance",
                "description": re.sub(r" +", " ", head + " " + tail),
            }
        else:
            print("Unexpected format! {!r}".format(s))
            return {
                "type": head,
                "description": tail,
            }


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


def read_tsv(file):
    """Reads and parses a TSV file, generating Transaction objects.

    This is a convenience function that simplifies parsing of ABN AMRO TSV files.
    Given a file-like object (or any iterable that returns lines), this function
    yields (generates) one Transaction object for each row.

    For ease-of-use, it also ignores empty lines and comment lines.
    """
    order = 0
    for r in csv.reader(filter_comments(file), dialect="excel-tab"):
        row = RowTuple(*r)
        order += 1
        cur = Currency(row.mutationcode)
        yield Transaction(
            account=int(row.accountNumber),
            date=datetime.strptime(row.transactiondate, "%Y%m%d").date(),
            # Ignoring row.valuedate.
            order=order,
            currency=cur,
            amount=Money(row.amount.replace(",", "."), cur),
            start_saldo=Money(row.startsaldo.replace(",", "."), cur),
            end_saldo=Money(row.endsaldo.replace(",", "."), cur),
            description=rejoin_description(row.description),
        )


def convert_tsv_to_json_like(filename):
    """Stupidly simple and easy-to-use function.

    Given a filename, it will parse all the transactions and return a list of
    dicts, a structure that can be easily serialized as JSON for later storage
    (or later processing using jq).
    """
    with open(os.path.expanduser(filename)) as f:
        return [r.as_json_like for r in read_tsv(f)]


if __name__ == "__main__":
    import doctest

    doctest.testmod()
