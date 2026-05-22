import re
import requests


url = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"

params = {
    "pil": "AFDIND",
    "sdate": "2025-01-01",
    "edate": "2025-01-05",
    "limit": 3,
    "fmt": "text",
}

response = requests.get(
    url,
    params=params,
    timeout=30,
)

text = response.text

print("URL:", response.url)
print("Status:", response.status_code)


# ---------------------------------------------------
# TEST 1:
# Show where each record begins
# ---------------------------------------------------

HEADER_RE = re.compile(
    r"""
    (?P<seq>\d{3})\s+
    (?P<wmo>[A-Z]{4}\d{2})\s+
    (?P<office>[A-Z]{4})\s+
    (?P<ddhhmm>\d{6})\s+
    (?P<pil>[A-Z]{6})
    """,
    re.VERBOSE,
)

matches = list(HEADER_RE.finditer(text))

print("\nFOUND RECORDS:", len(matches))

for i, match in enumerate(matches):
    print("\n-----------------------------")
    print(f"Record #{i + 1}")
    print("-----------------------------")

    print("Sequence:", match.group("seq"))
    print("WMO:", match.group("wmo"))
    print("Office:", match.group("office"))
    print("Time:", match.group("ddhhmm"))
    print("PIL:", match.group("pil"))

    print("\nHeader:")
    print(match.group(0))


# ---------------------------------------------------
# TEST 2:
# Actually split into records
# ---------------------------------------------------

records = []

for i, match in enumerate(matches):
    start = match.start()

    if i + 1 < len(matches):
        end = matches[i + 1].start()
    else:
        end = len(text)

    chunk = text[start:end].strip()

    records.append(chunk)

print("\n\nTOTAL SPLIT RECORDS:", len(records))


# ---------------------------------------------------
# TEST 3:
# Print summary of each record
# ---------------------------------------------------

for i, record in enumerate(records):
    print("\n=================================================")
    print(f"RECORD {i + 1}")
    print("=================================================")

    lines = record.splitlines()

    print("\nFIRST 15 LINES:\n")

    for line in lines[:15]:
        print(line)

    print("\nCHAR COUNT:", len(record))


# ---------------------------------------------------
# TEST 4:
# Extract issued time
# ---------------------------------------------------

ISSUED_RE = re.compile(
    r"Issued at .*"
)

for i, record in enumerate(records):
    issued_match = ISSUED_RE.search(record)

    print("\n-----------------------------")
    print(f"Record {i + 1} Issued Time")
    print("-----------------------------")

    if issued_match:
        print(issued_match.group(0))
    else:
        print("No issued time found")


# ---------------------------------------------------
# TEST 5:
# Extract KEY MESSAGES section
# ---------------------------------------------------

KEY_RE = re.compile(
    r"\.KEY MESSAGES\.\.\.(.*?)&&",
    re.DOTALL,
)

for i, record in enumerate(records):
    key_match = KEY_RE.search(record)

    print("\n-----------------------------")
    print(f"Record {i + 1} KEY MESSAGES")
    print("-----------------------------")

    if key_match:
        print(key_match.group(1).strip())
    else:
        print("No KEY MESSAGES found")