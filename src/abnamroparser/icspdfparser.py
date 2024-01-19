import datetime
import re
from .util import interval, first, money_format
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
from moneyed import Currency, Money
from pypdf import PdfReader


MONTHS_LONG = {
    name: nr
    for nr, name in enumerate(
        [
            "januari",
            "februari",
            "maart",
            "april",
            "mei",
            "juni",
            "juli",
            "augustus",
            "september",
            "oktober",
            "november",
            "december",
        ],
        start=1,
    )
}

MONTHS_SHORT = {
    name: nr
    for nr, name in enumerate(
        [
            "jan",
            "feb",
            "mrt",  # ← This one is not the first three letters.
            "apr",
            "mei",
            "jun",
            "jul",
            "aug",
            "sep",
            "okt",
            "nov",
            "dec",
        ],
        start=1,
    )
}


@dataclass
class Transaction:
    """Dataclass for each of the transactions from ICS credit card PDF files."""

    # The last four digits of the card.
    card_number: int

    # The transaction date. Transactions are ordered by this date.
    # I'm ignoring the "Datum boeking", as it is either the same day or the next working day.
    date: datetime.date

    # There are two or four strings of description.
    descriptions: list[str]

    # Three-letter country code.
    country_code: str

    # These are only available if the transaction was done in a foreign currency.
    foreign_amount: Money
    exchange_rate: float

    amount: Money

    @property
    def amount_formatted(self):
        """The amount of money, formatted in a simple way."""
        return money_format(self.amount)

    @property
    def foreign_amount_formatted(self):
        """The amount of money, formatted in a simple way."""
        if self.foreign_amount is None:
            return ""
        else:
            return money_format(self.foreign_amount)

    @property
    def foreign_currency_code(self):
        if self.foreign_amount is None:
            return ""
        else:
            return self.foreign_amount.currency.code

    @property
    def as_json_like(self):
        """Returns a JSON-serializable object.

        Returns a dict containing only data types that can be serialized as JSON:
        lists, dicts, strings, numbers, booleans.
        """
        return {
            "card_number": self.card_number,
            "date": self.date.isoformat(),
            "descriptions": list(self.descriptions),
            "country_code": self.country_code,
            "foreign_amount": self.foreign_amount_formatted,
            "foreign_currency": self.foreign_currency_code,
            "exchange_rate": self.exchange_rate,
            "amount": self.amount_formatted,
        }


def group_related_rows(table):
    """Returns the rows from the table, grouped by their semantic.

    >>> dt = datetime.date(2023, 1, 1)
    >>> table = [
    ...     [dt, "foobar1"],
    ...     [dt, "foobar2"],
    ...     ["Uw Card met als laatste vier cijfers 1234"],
    ...     ["J SMITH VAN DE FOOBAR"],
    ...     [dt, "foobar3"],
    ...     ["", "Wisselkoers USD"],
    ...     [dt, "foobar4"],
    ...     [dt, "foobar5"],
    ...     [dt, "foobar6"],
    ...     ["", "Wisselkoers BRL"],
    ... ]
    >>> it = group_related_rows(table)
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar1']]
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar2']]
    >>> next(it)
    [['Uw Card met als laatste vier cijfers 1234'], ['J SMITH VAN DE FOOBAR']]
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar3'], ['', 'Wisselkoers USD']]
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar4']]
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar5']]
    >>> next(it)
    [[datetime.date(2023, 1, 1), 'foobar6'], ['', 'Wisselkoers BRL']]
    >>> next(it)
    Traceback (most recent call last):
      ...
    StopIteration
    """
    try:
        it = iter(table)
        buffer = []
        while True:
            row = next(it)
            if isinstance(row[0], datetime.date) or row[0].startswith("Uw Card"):
                if len(buffer) > 0:
                    yield buffer
                buffer = [row]
            else:
                buffer.append(row)
    except StopIteration:
        if len(buffer) > 0:
            yield buffer


def get_transactions_from_pages(pages):
    """Given a sequence of pages (or a generator), yields transactions.

    This function has some sanity checks, expecting to receive all the pages
    from one single PDF.

    >>> # This doctest is huge and ugly. But I'm glad I've added it.
    >>> page1 = Page(1)
    >>> page1.date = datetime.date(2023, 2, 1)
    >>> page1.page_nr = 1
    >>> page1.table = {
    ...     789: ["09 dec", "09 dec", "GEINCASSEERD VORIG SALDO", "", "", "", "", "500,00", "Bij"],
    ...     678: ["Uw Card met als laatste vier cijfers 1234", "", "", "", "", "", "", "", ""],
    ...     567: ["J SMITH VAN DE FOOBAR", "", "", "", "", "", "", "", ""],
    ...     456: ["02 jan", "03 jan", "Description here", "Foobar", "NLD", "", "", "100,00", "Af"],
    ...     345: ["03 jan", "03 jan", "Foreign purchase", "Whatever", "USA", "6,05", "USD", "5,59", "Af"],
    ...     234: ["", "", "Wisselkoers USD", "1,08229", "", "", "", "", ""],
    ...     123: ["04 jan", "04 jan", "Blah blah blah", "Fizzbuzz", "LUX", "", "", "6,99", "Af"],
    ... }
    >>> page2 = Page(2)
    >>> page2.date = datetime.date(2023, 2, 1)
    >>> page2.page_nr = 2
    >>> page2.table = {
    ...     789: ["05 jan", "05 jan", "Boring stuff", "123456789", "NLD", "", "", "1.234,56", "Af"],
    ...     678: ["06 jan", "06 jan", "Money back", "Fizzbuzz", "LUX", "", "", "6,99", "Bij"],
    ...     567: ["Uw Card met als laatste vier cijfers 5678", "", "", "", "", "", "", "", ""],
    ...     456: ["M SMITH VAN DE FOOBAR", "", "", "", "", "", "", "", ""],
    ...     345: ["03 jan", "03 jan", "Foreign stuff", "Hello", "USA", "6,05", "USD", "5,59", "Af"],
    ...     234: ["", "", "Wisselkoers USD", "1,08229", "", "", "", "", ""],
    ...     123: ["05 jan", "05 jan", "Is it over", "Yet", "NLD", "", "", "1,99", "Af"],
    ... }
    >>> for t in get_transactions_from_pages([page1, page2]):
    ...    print(t)
    Transaction(card_number=None, date=datetime.date(2022, 12, 9), descriptions=['GEINCASSEERD VORIG SALDO', ''], country_code='', foreign_amount=None, exchange_rate=None, amount=Money('500.00', 'EUR'))
    Transaction(card_number='1234', date=datetime.date(2023, 1, 2), descriptions=['Description here', 'Foobar'], country_code='NLD', foreign_amount=None, exchange_rate=None, amount=Money('-100.00', 'EUR'))
    Transaction(card_number='1234', date=datetime.date(2023, 1, 3), descriptions=['Foreign purchase', 'Whatever'], country_code='USA', foreign_amount=Money('6.05', 'USD'), exchange_rate=1.08229, amount=Money('-5.59', 'EUR'))
    Transaction(card_number='1234', date=datetime.date(2023, 1, 4), descriptions=['Blah blah blah', 'Fizzbuzz'], country_code='LUX', foreign_amount=None, exchange_rate=None, amount=Money('-6.99', 'EUR'))
    Transaction(card_number='1234', date=datetime.date(2023, 1, 5), descriptions=['Boring stuff', '123456789'], country_code='NLD', foreign_amount=None, exchange_rate=None, amount=Money('-1234.56', 'EUR'))
    Transaction(card_number='1234', date=datetime.date(2023, 1, 6), descriptions=['Money back', 'Fizzbuzz'], country_code='LUX', foreign_amount=None, exchange_rate=None, amount=Money('6.99', 'EUR'))
    Transaction(card_number='5678', date=datetime.date(2023, 1, 3), descriptions=['Foreign stuff', 'Hello'], country_code='USA', foreign_amount=Money('6.05', 'USD'), exchange_rate=1.08229, amount=Money('-5.59', 'EUR'))
    Transaction(card_number='5678', date=datetime.date(2023, 1, 5), descriptions=['Is it over', 'Yet'], country_code='NLD', foreign_amount=None, exchange_rate=None, amount=Money('-1.99', 'EUR'))
    """
    # Part 1: Concatenating the tables from all pages into one single table.
    # This also involves properly converting the strings to a better format.
    statement_date = None
    table = []
    for nr, page in enumerate(pages, start=1):
        # Sanity check.
        assert nr == page.page_nr, "Pages must be in order."

        if nr == 1:
            statement_date = page.date
        else:
            # Sanity check.
            assert statement_date == page.date, "All pages must have the same date."

        # Converting each row, and appending to our accumulator table.
        for raw_row in page.table_as_list:
            if len(raw_row[0]) > Page.COLUMNS[0].max_length:
                # Special case for text that expands across all columns.
                assert all(t.strip() == "" for t in raw_row[1:])
                row = [raw_row[0].strip()]
            else:
                # Converting each cell, if the cell is not empty.
                row = [
                    page.convert_cell_text(cell, col.convert_method)
                    for (cell, col) in zip(raw_row, Page.COLUMNS)
                ]
            table.append(row)

    # Part 1 is finished.
    # Now `tables` is a list of lists,
    # being a concatenation of all the rows from all the pages.

    # Part 2: Generating the Transaction objects.
    card_number = None
    card_name = None
    for rows in group_related_rows(table):
        assert 0 < len(rows) <= 2

        first = rows[0]
        second = rows[1] if len(rows) > 1 else []

        if len(first) == len(second) == 1:
            card_number = re.match(r"Uw Card met als laatste vier cijfers ([0-9]+)", first[0]).group(1)
            card_name = second[0]
        elif len(first) == 9 and len(second) in [0, 9]:
            if first[5] == first[6] == "" and len(second) == 0:
                # Single-row transaction.
                yield Transaction(
                    card_number=card_number,  # I'm discarding the card name.
                    date=first[0],  # I'm discarding the "Datum boeking".
                    descriptions=[first[2], first[3]],
                    country_code=first[4],
                    amount=Money(first[8] + first[7], "EUR"),
                    foreign_amount=None,
                    exchange_rate=None,
                )
            elif first[5] and first[6] and len(second) == 9:
                # Sanity checks.
                assert (
                    ""
                    == second[0]
                    == second[1]
                    == second[4]
                    == second[5]
                    == second[6]
                    == second[7]
                    == second[8]
                )
                assert second[2] == "Wisselkoers {}".format(first[6])

                # Double-row transaction (foreign currency).
                yield Transaction(
                    card_number=card_number,  # I'm discarding the card name.
                    date=first[0],  # I'm discarding the "Datum boeking".
                    descriptions=[first[2], first[3]],
                    country_code=first[4],
                    amount=Money(first[8] + first[7], "EUR"),
                    foreign_amount=Money(first[5], first[6]),
                    exchange_rate=float(second[3].replace(",", ".")),
                )
            else:
                assert False, "This shouldn't happen. Processing {!r}".format(rows)
        else:
            assert False, "This shouldn't happen. Processing {!r}".format(rows)


TableColumn = namedtuple("TableColumn", ["x_interval", "max_length", "name", "convert_method"])


@dataclass
class Page:
    nr: int
    date: datetime.date = field(default=None, init=False)
    page_nr: int = field(default=None, init=False)
    total_pages: int = field(default=None, init=False)

    # Description of each of the columns from the main table.
    COLUMNS = [
        # dd mmm
        TableColumn(interval(59, 62), 6, "Datum transactie", "convert_date"),
        TableColumn(interval(102, 106), 6, "Datum boeking", "convert_date"),
        # The first description column is usually up to 22 characters,
        # but special strings go over that limit:
        # "GEINCASSEERD VORIG SALDO"
        # "TERUGGAVE BIJDR EXTRACARD"
        # "Corr Bijdrage Card Alert"
        TableColumn(interval(149, 155), 24, "Omschrijving", None),
        TableColumn(interval(275, 277), 13, "Description part 2", None),
        TableColumn(interval(362, 364), 3, "Country code", None),
        TableColumn(interval(401, 440), 8, "Bedrag in vreemde valuta", "convert_amount"),
        TableColumn(interval(444, 446), 3, "Currency code", None),
        TableColumn(interval(478, 530), 8, "Bedrag in euro's", "convert_amount"),
        TableColumn(interval(535, 539), 3, "Bij/Af", "convert_bij_af"),
    ]

    def __post_init__(self):
        self.table = defaultdict(lambda: [""] * len(self.COLUMNS))

    def convert_date(self, text):
        """Converts a date from `dd mmm` format.

        Uses the current page date to figure out the correct year.

        >>> page = Page(1)
        >>> page.date = datetime.date(2023, 1, 1)
        >>> page.convert_date("01 jan")
        datetime.date(2023, 1, 1)
        >>> page.convert_date("1 jan")
        datetime.date(2023, 1, 1)
        >>> page.convert_date("31 dec")
        datetime.date(2022, 12, 31)
        >>> page.convert_date("09 dec")
        datetime.date(2022, 12, 9)
        >>> page.convert_date("9 dec")
        datetime.date(2022, 12, 9)
        >>> page.convert_date("25 nov")
        datetime.date(2022, 11, 25)

        >>> page = Page(1)
        >>> page.date = datetime.date(2023, 2, 2)
        >>> page.convert_date("01 feb")
        datetime.date(2023, 2, 1)
        >>> page.convert_date("1 feb")
        datetime.date(2023, 2, 1)
        >>> page.convert_date("20 jan")
        datetime.date(2023, 1, 20)
        >>> page.convert_date("01 jan")
        datetime.date(2023, 1, 1)
        >>> page.convert_date("1 jan")
        datetime.date(2023, 1, 1)
        >>> page.convert_date("31 dec")
        datetime.date(2022, 12, 31)
        >>> page.convert_date("09 dec")
        datetime.date(2022, 12, 9)
        >>> page.convert_date("9 dec")
        datetime.date(2022, 12, 9)

        >>> page = Page(1)
        >>> page.date = datetime.date(2023, 6, 1)
        >>> page.convert_date("01 jun")
        datetime.date(2023, 6, 1)
        >>> page.convert_date("1 jun")
        datetime.date(2023, 6, 1)
        >>> page.convert_date("20 mei")
        datetime.date(2023, 5, 20)
        >>> page.convert_date("01 mei")
        datetime.date(2023, 5, 1)
        >>> page.convert_date("1 mei")
        datetime.date(2023, 5, 1)
        >>> page.convert_date("28 apr")
        datetime.date(2023, 4, 28)
        >>> page.convert_date("08 apr")
        datetime.date(2023, 4, 8)
        >>> page.convert_date("8 apr")
        datetime.date(2023, 4, 8)
        """
        dd, mmm = text.split()
        d = int(dd)
        m = MONTHS_SHORT[mmm]
        y1 = self.date.year
        y2 = self.date.year - 1

        dt1 = datetime.date(y1, m, d)
        dt2 = datetime.date(y2, m, d)

        diff1 = self.date - dt1
        diff2 = self.date - dt2

        if abs(diff1.days) < abs(diff2.days):
            return dt1
        else:
            return dt2

    @staticmethod
    def convert_bij_af(text):
        """Returns "+" for credit and "-" for debit.

        >>> Page.convert_bij_af("Af")
        '-'
        >>> Page.convert_bij_af("Bij")
        '+'
        """
        return {
            "Bij": "+",
            "Af": "-",
        }[text]

    @staticmethod
    def convert_amount(text):
        """Converts the localized amount to a machine-friendly representation.

        >>> Page.convert_amount("0,01")
        '0.01'
        >>> Page.convert_amount("123,45")
        '123.45'
        >>> Page.convert_amount("1.234.567,89")
        '1234567.89'
        """
        return text.replace(".", "").replace(",", ".")

    def convert_cell_text(self, text, method):
        """Converts the cell value, given a certain method.

        text.strip() is optional, as the cell values don't have any leading or
        trailing whitespace. But it doesn't hurt to .strip() it anyway.

        >>> page = Page(1)
        >>> page.date = datetime.date(2023, 7, 1)
        >>> page.convert_cell_text(" 20 jun ", "convert_date")
        datetime.date(2023, 6, 20)
        >>> page.convert_cell_text(" 9.876,54 ", "convert_amount")
        '9876.54'
        >>> page.convert_cell_text(" foobar ", None)
        'foobar'
        >>> page.convert_cell_text(" Bij ", "convert_bij_af")
        '+'
        >>> page.convert_cell_text(" Af ", "convert_bij_af")
        '-'
        >>> page.convert_cell_text("  ", "convert_amount")
        ''
        >>> page.convert_cell_text("  ", None)
        ''
        >>> page.convert_cell_text("  ", "convert_bij_af")
        ''
        """
        text = text.strip()
        if text == "":
            return ""
        elif method is None:
            return text
        else:
            return getattr(self, method)(text)

    @property
    def table_as_list(self):
        return [row for (y, row) in sorted(self.table.items(), reverse=True)]

    def table_as_string(self, sep="|", prefix="|", suffix="|", padding=True):
        """Returns a string representation of the table.

        Useful for debugging, and also works as a quick converter to CSV.

        >>> page = Page(1)
        >>> page.table = {
        ...     789: ["09 dec", "09 dec", "GEINCASSEERD VORIG SALDO", "", "", "", "", "500,00", "Bij"],
        ...     678: ["Uw Card met als laatste vier cijfers 1234", "", "", "", "", "", "", "", ""],
        ...     567: ["J SMITH VAN DE FOOBAR", "", "", "", "", "", "", "", ""],
        ...     456: ["02 jan", "03 jan", "Description here", "Foobar", "NLD", "", "", "100,00", "Af"],
        ...     345: ["03 jan", "03 jan", "Foreign purchase", "Whatever", "USA", "6,05", "USD", "5,59", "Af"],
        ...     234: ["", "", "Wisselkoers USD", "1,08229", "", "", "", "", ""],
        ...     123: ["04 jan", "04 jan", "Blah blah blah", "Fizzbuzz", "LUX", "", "", "6,99", "Af"],
        ... }
        >>> print(page.table_as_string())
        |09 dec|09 dec|GEINCASSEERD VORIG SALDO|             |   |        |   |500,00  |Bij|
        |Uw Card met als laatste vier cijfers 1234                                         |
        |J SMITH VAN DE FOOBAR                                                             |
        |02 jan|03 jan|Description here        |Foobar       |NLD|        |   |100,00  |Af |
        |03 jan|03 jan|Foreign purchase        |Whatever     |USA|6,05    |USD|5,59    |Af |
        |      |      |Wisselkoers USD         |1,08229      |   |        |   |        |   |
        |04 jan|04 jan|Blah blah blah          |Fizzbuzz     |LUX|        |   |6,99    |Af |
        >>> print(page.table_as_string(sep='", "', prefix='["', suffix='"],', padding=False))
        ["09 dec", "09 dec", "GEINCASSEERD VORIG SALDO", "", "", "", "", "500,00", "Bij"],
        ["Uw Card met als laatste vier cijfers 1234"],
        ["J SMITH VAN DE FOOBAR"],
        ["02 jan", "03 jan", "Description here", "Foobar", "NLD", "", "", "100,00", "Af"],
        ["03 jan", "03 jan", "Foreign purchase", "Whatever", "USA", "6,05", "USD", "5,59", "Af"],
        ["", "", "Wisselkoers USD", "1,08229", "", "", "", "", ""],
        ["04 jan", "04 jan", "Blah blah blah", "Fizzbuzz", "LUX", "", "", "6,99", "Af"],
        >>> print(page.table_as_string(sep="│", prefix="║", suffix="║", padding=True))
        ║09 dec│09 dec│GEINCASSEERD VORIG SALDO│             │   │        │   │500,00  │Bij║
        ║Uw Card met als laatste vier cijfers 1234                                         ║
        ║J SMITH VAN DE FOOBAR                                                             ║
        ║02 jan│03 jan│Description here        │Foobar       │NLD│        │   │100,00  │Af ║
        ║03 jan│03 jan│Foreign purchase        │Whatever     │USA│6,05    │USD│5,59    │Af ║
        ║      │      │Wisselkoers USD         │1,08229      │   │        │   │        │   ║
        ║04 jan│04 jan│Blah blah blah          │Fizzbuzz     │LUX│        │   │6,99    │Af ║
        """
        total_width = sum(c.max_length for c in self.COLUMNS) + len(self.COLUMNS) - 1
        lines = []
        for row in self.table_as_list:
            if len(row[0]) > self.COLUMNS[0].max_length:
                # Special case for text that expands across all columns.
                assert all(t == "" for t in row[1:])
                lines.append(row[0].ljust(total_width if padding else 0, " "))
            else:
                # Normal case, each column is well-behaved.
                lines.append(
                    sep.join(
                        cell.ljust(self.COLUMNS[i].max_length if padding else 0, " ")
                        for (i, cell) in enumerate(row)
                    )
                )
        return "\n".join(prefix + line + suffix for line in lines)

    def visitor(self, text, cm, tm, font_dict, font_size):
        """Callback function passed to PdfReader.extract_text().

        This method gets called on each text object from the PDF.
        This method populates `self.table` (and other attributes) based on the PDF contents.
        """

        def print_debug():
            print("DEBUG: {!r}".format(self))
            print(
                "DEBUG: {font_size!s:4} {cm!s:36} {tm!s:36} {text!r}".format(
                    cm=cm,  # Current user matrix.
                    tm=tm,  # Text matrix.
                    # font_dict=font_dict,  # I don't care.
                    font_size=font_size,
                    text=text,
                )
            )
            return ""

        if text.strip() == "":
            # Do nothing if this is just empty text.
            return

        if font_size == 6.0:
            # Very small footer text that is not relevant.
            return

        if re.match(
            "|".join(
                [
                    # This text shows up every month:
                    r"Uw betalingen aan International Card Services BV zijn bijgewerkt",
                    r"Het totale saldo ad.*zal omstreeks",
                    r"(machtigingsnummer )?E[0-9]+ worden geïncasseerd",
                    # This text used to show up, but not anymore:
                    r"Wilt u een overboeking doen naar uw Card-rekening",
                    r"Diemen. Vermeld bij uw betaling altijd uw ICS-klantnummer",
                    # Advertisement:
                    r"Nu beschikbaar: Apple Pay! Voeg eenvoudig uw Card aan uw Apple Wallet toe in onze app.",
                    # Als u online een product besteld heeft, bent u er natuurlijk zuinig op. Maar een ongeluk zit in een klein hoekje. Betaal daarom altijd met uw ABN AMRO creditcard. Want dan heeft u een Aankoopverzekering. Kijk voor meer informatie en de voorwaarden op www.zekermetjecreditcard.nl.
                    r"Als u online een product besteld heeft, bent u er natuurlijk",
                    r"zuinig op. Maar een ongeluk zit in een klein hoekje",
                    r"daarom altijd met uw ABN AMRO creditcard",
                    r"een Aankoopverzekering. Kijk voor meer informatie",
                    r"voorwaarden op www.zekermetjecreditcard.nl",
                ]
            ),
            text,
        ):
            # Useless messages.
            return

        # Sanity check, guarding ourselves against future PDF changes.
        assert font_size == 8.0, "This code expects the same font size for all text." + print_debug()

        # cm is the current user matrix.
        # tm is the text matrix.
        # For these PDF files, cm is always constant, and tm changes.
        x = tm[-2]  # x increases from left to right →
        y = tm[-1]  # y increases from bottom to top ↑

        # Sanity check.
        assert cm == [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "Has the PDF format changed?" + print_debug()
        assert tm == [1.0, 0.0, 0.0, 1.0, x, y], "Has the PDF format changed?" + print_debug()

        # ICS company address and other information about that company.
        y_company_info = interval(755, 9999) if self.nr == 1 else interval(0, 0)
        # Date, number of pages, etc.
        y_statement_info = interval(665, 721) if self.nr == 1 else interval(665, 721)
        # The list of transactions.
        y_main_table = interval(126, 645) if self.nr == 1 else interval(0, 645)
        # Credit limit and the minimal payment.
        y_footer_info = interval(0, 126) if self.nr == 1 else interval(0, 0)

        if y in y_company_info:
            # ICS company name, address, telephone, website, etc.
            return

        elif y in y_statement_info:
            # Y↴  X→60.0            156.22   177                  272.86   294             391.22   411 420         507.22
            #     ┌─────────────────────── ┌──────────────────────────── ┌─────────────────────── ┌───────────────────────
            # 720 │ Datum                  │ ICS-klantnummer             │ Volgnummer             │ Bladnummer
            # 709 │ 1 januari 2024         │ 12345678901                 │ 1                      │ 2 van 2
            #     ┌─────────────────────── ┌──────────────────────────── ┌─────────────────────── ┌───────────────────────
            # 696 │ Vorig openstaand saldo │ Totaal ontvangen betalingen │ Totaal nieuwe uitgaven │ Nieuw openstaand saldo
            # 686 │ € 123,00            Af │ € 123,00                Bij │ € 456,00            Af │ € 456,00            Af
            if 59 <= x <= 61 and 708 <= y <= 710:
                # Parse date.
                d, mm, y = text.split()
                m = MONTHS_LONG[mm]
                self.date = datetime.date(int(y), m, int(d))
            elif 410 <= x <= 412 and 708 <= y <= 710:
                # Current page
                self.page_nr = int(text)
                assert self.page_nr == self.nr, "Page number doesn't match." + print_debug()
            elif 420 <= x and 708 <= y <= 710:
                # Total pages
                _, _, total = text.partition("van ")
                self.total_pages = int(total)
            else:
                # Ignoring everything else.
                # If you really want, feel free to add code to parse them.
                return

        elif y in y_main_table:
            # | Header| Data  | Column header:                                   |
            # |-------|-------|--------------------------------------------------|
            # | X= 61 | X= 60 | Datum transactie                                 |
            # | X=105 | X=103 | Datum boeking                                    |
            # | X=154 | X=150 | Omschrijving                                     |
            # |       | X=276 | (some other code)                                |
            # |       | X=363 | three-letter country code                        |
            # | X=402 | X=... | Bedrag in vreemde valuta (data is right-aligned) |
            # |       | X=445 | Currency code                                    |
            # | X=479 | X=... | Bedrag in euro's         (data is right-aligned) |
            # |       |X=537±1| Bij/Af (credit/debit)    (data is right-aligned) |
            y_header = interval(633, 645)
            if y in y_header:
                # Ignoring the table header cells.
                return

            # If you need debugging:
            # print("X={:>6} Y={:>6} {!r}".format(x, y, text))

            column = first(n for n, col in enumerate(self.COLUMNS) if x in col.x_interval)
            assert column is not None, "Unmatched column" + print_debug()
            self.table[y][column] = text

        elif y in y_footer_info:
            # Dit product valt onder het depositogarantiestelsel. Meer informatie vindt u op www.icscards.nl/abnamro/info/depositogarantiestelsel en op het informatieblad dat u jaarlijks ontvangt.
            return

        else:
            # Sanity check, in case the PDF format changes.
            assert False, "Y={} should have matched one of the ranges.".format(y) + print_debug()


def read_ics_pdf(filename):
    reader = PdfReader(filename)
    pages = []
    for nr, page in enumerate(reader.pages, start=1):
        p = Page(nr)
        pages.append(p)
        # Ignoring the returned string from extract_text().
        page.extract_text(visitor_text=p.visitor)

    yield from get_transactions_from_pages(pages)
