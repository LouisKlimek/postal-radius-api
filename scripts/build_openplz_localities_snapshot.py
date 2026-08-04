#!/usr/bin/env python3
"""Build an image-local DACH locality snapshot from OpenPLZ Locality entities.

The OpenPLZ Localities endpoint is the classification source: only returned
Locality entities may be included. GeoNames is consulted only to enrich an
already accepted exact (country, postalCode, locality name) tuple with its
coordinate; unmatched localities are intentionally omitted.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://openplzapi.org/{country}/Localities"
COUNTRIES = ("DE", "AT", "CH")
PAGE_SIZE = 50


def fetch_json(url: str) -> tuple[object, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response), {key.lower(): value for key, value in response.headers.items()}


def source_coordinates(raw_data_file: Path) -> dict[tuple[str, str, str], tuple[str, str]]:
    with raw_data_file.open(encoding="utf-8", newline="") as source:
        return {
            (row["country"], row["postal_code"], row["city_name"]): (row["latitude"], row["longitude"])
            for row in csv.DictReader(source)
        }


def localities_for_country(country: str) -> list[dict[str, str]]:
    localities: list[dict[str, str]] = []
    for first_digit in range(10):
        page = 1
        total_pages = 1
        while page <= total_pages:
            query = urllib.parse.urlencode(
                {"postalCode": f"^{first_digit}", "page": page, "pageSize": PAGE_SIZE}
            )
            payload, headers = fetch_json(API_URL.format(country=country.lower()) + "?" + query)
            if not isinstance(payload, list):
                raise ValueError(f"OpenPLZ {country} page {page} returned a non-list response")
            total_pages = int(headers["x-total-pages"])
            for locality in payload:
                postal_code = locality.get("postalCode")
                name = locality.get("name")
                if not isinstance(postal_code, str) or not isinstance(name, str):
                    raise ValueError(f"OpenPLZ {country} returned an invalid Locality entity")
                localities.append({"country": country, "postal_code": postal_code, "city_name": name})
            page += 1
    return localities


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {Path(argv[0]).name} RAW_GEONAMES_CSV OUTPUT_CSV", file=sys.stderr)
        return 2

    coordinates = source_coordinates(Path(argv[1]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    source_count = 0
    for country in COUNTRIES:
        for locality in localities_for_country(country):
            source_count += 1
            coordinate = coordinates.get((locality["country"], locality["postal_code"], locality["city_name"]))
            if coordinate is None:
                continue
            key = locality["country"], locality["postal_code"]
            record = {**locality, "latitude": coordinate[0], "longitude": coordinate[1]}
            accepted[key] = min(record, accepted.get(key, record), key=lambda item: item["city_name"])

    output_file = Path(argv[2])
    with output_file.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("country", "postal_code", "city_name", "latitude", "longitude"))
        for _, record in sorted(accepted.items()):
            writer.writerow((
                record["country"],
                record["postal_code"],
                record["city_name"],
                record["latitude"],
                record["longitude"],
            ))

    print(f"OpenPLZ Locality entities fetched: {source_count}")
    print(f"Exact GeoNames coordinate enrichments written: {len(accepted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
