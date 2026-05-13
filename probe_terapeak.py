#!/usr/bin/env python3
"""probe_terapeak.py — Hit the Seller Hub Research (Terapeak) JSON endpoint
once and dump the response shape, so we can see exactly what it returns
before wiring it into the real scraper.

Setup:
  1) Add this line to your .env (one line, no quotes around the value):
       EBAY_TERAPEAK_COOKIE=<paste the entire cookie string from your curl>
     The cookie is the long string after `-b '...'` in the curl you copied
     from DevTools.  Keep it secret; treat it like a password.

Run:
  py -3.12 probe_terapeak.py

Output:
  terapeak_response.json   (pretty-printed full response)
  + a structural summary printed to the console
"""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

COOKIE = os.getenv("EBAY_TERAPEAK_COOKIE", "").strip()
if not COOKIE:
    print("ERROR: EBAY_TERAPEAK_COOKIE not set in .env", file=sys.stderr)
    sys.exit(1)

# Test query: 2017 Volkswagen Tiguan parts, last 90 days, Used, sorted by avg sale price
now_ms   = int(time.time() * 1000)
start_ms = now_ms - 90 * 24 * 60 * 60 * 1000

params = [
    ("marketplace", "EBAY-US"),
    ("keywords",    "Volkswagen 2017 Tiguan"),
    ("dayRange",    "90"),
    ("endDate",     str(now_ms)),
    ("startDate",   str(start_ms)),
    ("categoryId",  "6028"),       # Auto Parts & Accessories
    ("conditionId", "3000"),       # Used
    ("offset",      "0"),
    ("limit",       "50"),
    ("sorting",     "-avgsalesprice"),
    ("tabName",     "SOLD"),
    ("tz",          "America/Los_Angeles"),
    ("modules",     "aggregates"),
    ("modules",     "searchResults"),
    ("modules",     "resultsHeader"),
]

url = "https://www.ebay.com/sh/research/api/search?" + urlencode(params)

headers = {
    "accept":           "*/*",
    "accept-language":  "en-US,en;q=0.9",
    "cache-control":    "no-cache",
    "dnt":              "1",
    "pragma":           "no-cache",
    "priority":         "u=1, i",
    "referer":          "https://www.ebay.com/sh/research?marketplace=EBAY-US&keywords=test&tabName=SOLD",
    "sec-ch-ua":        '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":   "empty",
    "sec-fetch-mode":   "cors",
    "sec-fetch-site":   "same-origin",
    "user-agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
    "cookie":           COOKIE,
}

print(f"GET {url[:100]}...")
resp = requests.get(url, headers=headers, timeout=30)
print(f"HTTP {resp.status_code}  {len(resp.content):,} bytes  "
      f"content-type={resp.headers.get('content-type','?')}")

# Always save the raw response first so we can inspect even if parsing fails
with open("terapeak_raw.txt", "w", encoding="utf-8") as f:
    f.write(resp.text)
print(f"Saved terapeak_raw.txt ({len(resp.text):,} chars)")
print(f"First 200 chars: {resp.text[:200]!r}")

ct = resp.headers.get("content-type", "")
if "json" not in ct.lower():
    print("Non-JSON content-type — likely a login redirect or block. Inspect terapeak_raw.txt.")
    sys.exit(2)

# Try single-doc parse first, then NDJSON, then concatenated-docs (raw_decode loop)
data = None
try:
    data = resp.json()
    print("Parsed as single JSON document.")
except json.JSONDecodeError:
    pass

if data is None:
    # NDJSON: one JSON object per line
    try:
        lines = [ln for ln in resp.text.splitlines() if ln.strip()]
        docs = [json.loads(ln) for ln in lines]
        if len(docs) == 1:
            data = docs[0]
        else:
            data = {"_modules": docs}
        print(f"Parsed as NDJSON ({len(docs)} document(s)).")
    except json.JSONDecodeError:
        pass

if data is None:
    # Concatenated JSON docs without newlines
    try:
        decoder = json.JSONDecoder()
        text = resp.text.strip()
        idx = 0
        docs = []
        while idx < len(text):
            obj, end = decoder.raw_decode(text, idx)
            docs.append(obj)
            idx = end
            while idx < len(text) and text[idx] in " \r\n\t":
                idx += 1
        data = {"_modules": docs} if len(docs) > 1 else docs[0]
        print(f"Parsed as concatenated JSON ({len(docs)} document(s)).")
    except json.JSONDecodeError as e:
        print(f"Could not parse response as JSON: {e}")
        print("Inspect terapeak_raw.txt manually.")
        sys.exit(3)

with open("terapeak_response.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved terapeak_response.json")

# --- Summarize structure ---
def summarize(obj, depth=0, max_depth=4, prefix=""):
    pad = "  " * depth
    if depth > max_depth:
        print(f"{pad}{prefix}…(truncated)")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                kind = "dict" if isinstance(v, dict) else f"list[{len(v)}]"
                print(f"{pad}{prefix}{k!r}: {kind}")
                if isinstance(v, list) and v:
                    summarize(v[0], depth + 1, max_depth, prefix="[0] ")
                else:
                    summarize(v, depth + 1, max_depth)
            else:
                preview = repr(v)
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                print(f"{pad}{prefix}{k!r}: {type(v).__name__} = {preview}")
    elif isinstance(obj, list):
        print(f"{pad}{prefix}list[{len(obj)}]")
        if obj:
            summarize(obj[0], depth + 1, max_depth, prefix="[0] ")

print("\n--- response shape ---")
if isinstance(data, dict) and "_modules" in data:
    for i, mod in enumerate(data["_modules"]):
        mtype = mod.get("_type", "?") if isinstance(mod, dict) else type(mod).__name__
        print(f"\n=== module[{i}]  _type={mtype} ===")
        summarize(mod, max_depth=5)
else:
    summarize(data, max_depth=5)
