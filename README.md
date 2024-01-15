# abn-amro-tsv-parser

Parser for the Dutch [ABN AMRO bank](https://www.abnamro.nl/) transactions.

This project supports the `TXT*.TAB` files, which are tab-separated values. This project may still be useful while parsing the other available file formats.

## How to "install" this project

* Download the [abnamrotsvparser.py](abnamrotsvparser.py) and save it anywhere.
* Install the [required Python libraries](https://pip.pypa.io/en/stable/user_guide/#requirements-files). Consider using a [virtual environment](https://docs.python.org/3/library/venv.html) to keep everything tidy.

## How to use this project

```python
import abnamrotsvparser

# You can iterate over the transactions of a certain file:
with open("TXT_SAMPLE.TAB") as f:
    for transaction in abnamrotsvparser.read_tsv(f):
        print(
            "{!s:12} {:32} {}".format(
                transaction.amount,
                transaction.desc["type"],
                transaction.desc.get("Naam", "?"),
            )
        )

# Or you can easily convert it to a JSON file:
import json
with open("converted.json", "w") as f:
    json.dump(
        abnamrotsvparser.convert_tsv_to_json_like("TXT_SAMPLE.TAB"),
        f,
        indent=2,
        sort_keys=True,
    )
```

I encourage you to take a look at the source-code. It's full of [doctests](https://docs.python.org/3/library/doctest.html), so it should be easy to learn.

## About the available file formats

You can download your transactions from the ABN AMRO internet banking website:

1. Login to the [ABN AMRO Internet Banking](https://www.abnamro.nl/my-abnamro/self-service/overview/index.html) website.
2. At the top three tabs, go to **Self service**.
3. Choose **Download statements** as the topic.
4. Click on **Download transactions**. (And then click on the yellow button to open the actual page.)
    * Don't go to **Download account statements**, or to **Download other statements**, as those pages only allow downloading PDF files.
5. Select the period you want to download.
6. Select the format of the file you want to download.
    * For the purposes of this repository, please select **TXT**.
    * Personally, I think it's a good idea to download all available formats and save them for the future.
7. Check which accounts should be included.
8. Click on the yellow **download** button.

The ABN AMRO website offers the following file formats:

### PDF

Sample filename: `mutov123456789_01012022-31122022.pdf`

Basic PDF file, well formatted to look pretty in a paper statement.

### TXT

Sample filename: `TXT231231235959.TAB`

The date and time of the download is part of the filename, following the `TXT%y%m%d%H%M%S.TAB` format.

It is a tab-separated file without any headers. The columns are the same as the XLS Excel file, but in a slightly different order.

Use [abnamrotsvparser.py](abnamrotsvparser.py) to parse this kind of file.

### MT940

Sample filename: `MT940231231235959.STA`

The date and time of the download is part of the filename, following the `MT940%y%m%d%H%M%S.STA` format.

It is a plain text file in [MT940](https://en.wikipedia.org/wiki/MT940) format.

Use the [mt-940](https://github.com/WoLpH/mt940) ([PyPI](https://pypi.org/project/mt-940/), [docs](https://mt940.readthedocs.io/)) library to parse it.

### XLS

Sample filename: `XLS231231235959.xls`

The date and time of the download is part of the filename, following the `XLS%y%m%d%H%M%S.xls` format.

It is an Excel file with one transaction per row. It is equivalent to the TXT file, but with the columns in a slightly different order.

The first row has the headers:

* accountNumber - always the same
* mutationcode - the three-letter currency code
* transactiondate - in format `YYYYMMDD`
* valuedate - in format `YYYYMMDD`
* startsaldo - number
* endsaldo - number
* amount - number
* description - string, exactly the same as in the TXT format

### CAMT. 053

Sample filename: `2012345678_012345678901.zip`

It is a ZIP file full of XML files with this naming schema: `2012345678_AAAAAAAAA_DDMMYY000000.xml`, where `AAAAAAAAA` is the account number and `DDMMYY` is the date. Each file contains all the transactions for that date.

I have not investigated much this format. It's probably a good machine-readable format, if you manage to figure out the meaning behind the several XML elements.

However, despite being an XML of arbitrary length, some XML elements have an limit on the size of their strings, so some fields might be truncated. (e.g. `<Nm>…</Nm>` seems to be limited to 24 characters, while `/NAME/` from `<AddtlNtryInf>…</AddtlNtryInf>` contains the full name.)

### Comparison between the formats

|                         | PDF | TXT | MT940 | XLS  | CAMT. 053 |
|-------------------------|-----|-----|-------|------|-----------|
| Plain text              | No  | Yes | Yes   | No   | Zipped XML|
| Human-readable          | Yes |Mostly|Almost| Yes  | Hell, no! |
| Machine-readble         | No  | Yes | Yes   |Mostly| Yes       |
| Easy to concatenate     | No  | Yes | Yes   |Mostly| Depends   |
| Description case        |Mixed|Mixed|ALL CAPS|Mixed| Mixed     |
| Space every 32/64 chars | -   | Yes |Newline| No   | No        |
| Truncated string fields | No? | No? | No?   | No?  | Yes (some)|
| May benefit from this project's `parse_description()`| - | Yes | Probably | Yes | Yes |

* Because each transaction is broken into multiple lines, *MT940* is hard to manipulate by hand with a plain text editor.
* *MT940* having the description in ALL CAPS makes it uglier and less readable. That was the main reason why I decided to stop using MT940 and write my own TXT parser.
* *CAMT. 053* is very convoluted and hard to work with (unless you use specialized software). Even then, some fields may be truncated.
* It is always hard to extract information from *PDF* files.
* Having to deal with additional space characters in the *TXT* files is annoying.
* *XLS* is a proprietary binary format requiring specialized software.

Thus, there is no single "best" format, it depends on your needs:

* *TXT* is the best if you want to work with simple plain text files.
* *XLS* is the best all-around format if you don't mind reading from a proprietary binary format.
* *CAMT. 053* is adequate if you want machine-readable files without proprietary formats.

## Project status

I created this for my own uses. I'm sharing with the world because I believe
more people are in the same situation as me, and more people certainly want to
parse their own bank account statements.

That said, I don't plan on improving this code much. I'll fix it and update it
as much as I need for my own statements. Simple pull requests are welcome.

I cannot promise the API will be stable. 

I'm not planning on releasing this on [PyPI](https://pypi.org/). You are free
to fork this project and create a well-maintained version yourself.
