"""Offline DACH postal-code radius API."""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, request

DATA_FILE = Path(__file__).with_name("data") / "dach_postal_centroids_geonames_2026-08-03.csv"
EARTH_RADIUS_KM = 6371.0088
VALID_COUNTRIES = {"DE", "AT", "CH"}
VALID_ORDERS = {"asc", "desc"}


@lru_cache(maxsize=1)
def load_postal_codes() -> tuple[dict[tuple[str, str], dict[str, object]], tuple[dict[str, object], ...]]:
    by_postal_code: dict[tuple[str, str], dict[str, object]] = {}
    with DATA_FILE.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            record = {
                "country": row["country"],
                "postal_code": row["postal_code"],
                "city_name": row["city_name"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            }
            by_postal_code[(record["country"], record["postal_code"])] = record
    return by_postal_code, tuple(by_postal_code.values())


def error_response(status: int, code: str, message: str):
    return jsonify({"error": {"code": code, "message": message}}), status


def parse_query() -> tuple[dict[str, object] | None, tuple[object, int] | None]:
    country = request.args.get("country", "").strip().upper()
    postal_code = request.args.get("postal_code", "").strip()
    radius_value = request.args.get("radius_km", "").strip()
    order = request.args.get("order", "asc").strip().lower()

    if country not in VALID_COUNTRIES:
        return None, error_response(400, "invalid_request", "country must be one of DE, AT, CH")
    if not postal_code:
        return None, error_response(400, "invalid_request", "postal_code is required")
    if not radius_value.isdigit() or int(radius_value) <= 0:
        return None, error_response(400, "invalid_request", "radius_km must be a positive integer")
    if order not in VALID_ORDERS:
        return None, error_response(400, "invalid_request", "order must be asc or desc")
    return {
        "country": country,
        "postal_code": postal_code,
        "radius_km": int(radius_value),
        "order": order,
    }, None


def distance_km(first: dict[str, object], second: dict[str, object]) -> float:
    latitude_1, longitude_1 = math.radians(first["latitude"]), math.radians(first["longitude"])
    latitude_2, longitude_2 = math.radians(second["latitude"]), math.radians(second["longitude"])
    latitude_delta = latitude_2 - latitude_1
    longitude_delta = longitude_2 - longitude_1
    a = math.sin(latitude_delta / 2) ** 2 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(longitude_delta / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/v1/postal-codes/nearby")
    def nearby():
        query, query_error = parse_query()
        if query_error:
            return query_error
        assert query is not None

        by_postal_code, all_postal_codes = load_postal_codes()
        origin = by_postal_code.get((query["country"], query["postal_code"]))
        if origin is None:
            return error_response(404, "postal_code_not_found", "postal_code was not found for country")

        results = []
        for candidate in all_postal_codes:
            calculated_distance = distance_km(origin, candidate)
            if calculated_distance <= query["radius_km"]:
                results.append({
                    "country": candidate["country"],
                    "postal_code": candidate["postal_code"],
                    "city_name": candidate["city_name"],
                    "distance_km": round(calculated_distance, 3),
                })
        results.sort(
            key=lambda item: (item["distance_km"], item["country"], item["postal_code"]),
            reverse=query["order"] == "desc",
        )
        return jsonify({"query": query, "results": results})

    return app


app = create_app()
