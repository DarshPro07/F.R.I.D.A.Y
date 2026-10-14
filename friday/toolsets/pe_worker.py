"""
BIN-002: the parser child. binary.py copies this file, pe_parse.py and the
sample into a quarantine root and runs it there under the OS boundary; it is
never imported. No friday imports: the quarantine holds only these files.

Reads argv[1] as bytes and prints pe_parse.parse_pe's report as one JSON line.
"""

import json
import sys

import pe_parse

with open(sys.argv[1], "rb") as handle:
    data = handle.read()
print(json.dumps(pe_parse.parse_pe(data)))
