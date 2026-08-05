"""Offline DACH postal-code radius API."""

from __future__ import annotations

import csv
import math
import os
import threading
import time
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, request
from geographiclib.geodesic import Geodesic

RAW_DATA_FILE = Path(__file__).with_name("data") / "dach_postal_centroids_geonames_2026-08-03.csv"
LOCALITIES_DATA_FILE = Path(__file__).with_name("data") / "dach_localities_openplz_2026-08-04.csv"
EARTH_RADIUS_KM = 6371.0088
VALID_COUNTRIES = {"DE", "AT", "CH"}
VALID_ORDERS = {"asc", "desc"}
GRID_CELL_DEGREES = 1.0
DEFAULT_MAX_RADIUS_KM = 500
DEFAULT_LIMIT = 1000
MAX_LIMIT = 1000
DEFAULT_RATE_LIMIT_PER_MINUTE = 120


@lru_cache(maxsize=2)
def load_postal_codes(data_file: Path = RAW_DATA_FILE) -> tuple[
    dict[tuple[str, str], dict[str, object]],
    tuple[dict[str, object], ...],
    dict[tuple[int, int], tuple[dict[str, object], ...]],
]:
    by_postal_code: dict[tuple[str, str], dict[str, object]] = {}
    grid: dict[tuple[int, int], list[dict[str, object]]] = {}
    with data_file.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            record = {
                "country": row["country"],
                "postal_code": row["postal_code"],
                "city_name": row["city_name"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            }
            by_postal_code[(record["country"], record["postal_code"])] = record

    all_postal_codes = tuple(by_postal_code.values())
    for record in all_postal_codes:
        cell = (
            math.floor(record["latitude"] / GRID_CELL_DEGREES),
            math.floor(record["longitude"] / GRID_CELL_DEGREES),
        )
        grid.setdefault(cell, []).append(record)
    return by_postal_code, all_postal_codes, {cell: tuple(records) for cell, records in grid.items()}


def bounding_box(origin: dict[str, object], radius_km: int) -> tuple[float, float, float, float]:
    latitude_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
    latitude = float(origin["latitude"])
    longitude = float(origin["longitude"])
    cosine_latitude = abs(math.cos(math.radians(latitude)))
    if cosine_latitude <= 0.01:
        longitude_delta = 180.0
    else:
        longitude_delta = math.degrees(math.asin(min(1.0, math.sin(radius_km / EARTH_RADIUS_KM) / cosine_latitude)))
    return (
        latitude - latitude_delta,
        latitude + latitude_delta,
        longitude - longitude_delta,
        longitude + longitude_delta,
    )


def grid_candidates(
    grid: dict[tuple[int, int], tuple[dict[str, object], ...]],
    minimum_latitude: float,
    maximum_latitude: float,
    minimum_longitude: float,
    maximum_longitude: float,
):
    for latitude_cell in range(
        math.floor(minimum_latitude / GRID_CELL_DEGREES),
        math.floor(maximum_latitude / GRID_CELL_DEGREES) + 1,
    ):
        for longitude_cell in range(
            math.floor(minimum_longitude / GRID_CELL_DEGREES),
            math.floor(maximum_longitude / GRID_CELL_DEGREES) + 1,
        ):
            for candidate in grid.get((latitude_cell, longitude_cell), ()):
                if (
                    minimum_latitude <= candidate["latitude"] <= maximum_latitude
                    and minimum_longitude <= candidate["longitude"] <= maximum_longitude
                ):
                    yield candidate


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None):
    response = jsonify({"error": {"code": code, "message": message}})
    if headers:
        response.headers.update(headers)
    return response, status


def configured_max_radius_km() -> int:
    value = os.getenv("MAX_RADIUS_KM", str(DEFAULT_MAX_RADIUS_KM)).strip()
    if value.isdigit() and int(value) > 0:
        return int(value)
    return DEFAULT_MAX_RADIUS_KM


def configured_rate_limit_per_minute() -> int:
    value = os.getenv("RATE_LIMIT_PER_MINUTE", str(DEFAULT_RATE_LIMIT_PER_MINUTE)).strip()
    if value.isdigit() and int(value) > 0:
        return int(value)
    return DEFAULT_RATE_LIMIT_PER_MINUTE


def configured_trusted_proxy_headers() -> bool:
    return os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in {"1", "true", "yes", "on"}


class ClientRateLimiter:
    """Fixed-window in-memory request counter for one application process."""

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self._clients: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def retry_after(self, client_ip: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            window_started, request_count = self._clients.get(client_ip, (now, 0))
            elapsed = now - window_started
            if elapsed >= 60:
                window_started, request_count = now, 0
            if request_count >= self.requests_per_minute:
                return max(1, math.ceil(60 - elapsed))
            self._clients[client_ip] = (window_started, request_count + 1)
            return None


def parse_query(max_radius_km: int) -> tuple[dict[str, object] | None, tuple[object, int] | None]:
    country = request.args.get("country", "").strip().upper()
    postal_code = request.args.get("postal_code", "").strip()
    radius_value = request.args.get("radius_km", "").strip()
    order = request.args.get("order", "asc").strip().lower()
    limit_value = request.args.get("limit", str(DEFAULT_LIMIT)).strip()
    offset_value = request.args.get("offset", "0").strip()
    localities_only_value = request.args.get("localities_only", "true").strip().lower()
    include_cross_border_value = request.args.get("include_cross_border", "false").strip().lower()

    if country not in VALID_COUNTRIES:
        return None, error_response(400, "invalid_request", "country must be one of DE, AT, CH")
    if not postal_code:
        return None, error_response(400, "invalid_request", "postal_code is required")
    if not radius_value.isdigit() or int(radius_value) <= 0:
        return None, error_response(400, "invalid_request", "radius_km must be a positive integer")
    if int(radius_value) > max_radius_km:
        return None, error_response(
            400,
            "invalid_request",
            f"radius_km must not exceed MAX_RADIUS_KM ({max_radius_km})",
        )
    if order not in VALID_ORDERS:
        return None, error_response(400, "invalid_request", "order must be asc or desc")
    if not limit_value.isdigit() or not 1 <= int(limit_value) <= MAX_LIMIT:
        return None, error_response(400, "invalid_request", "limit must be an integer between 1 and 1000")
    if not offset_value.isdigit():
        return None, error_response(400, "invalid_request", "offset must be a non-negative integer")
    if localities_only_value not in {"true", "false"}:
        return None, error_response(400, "invalid_request", "localities_only must be true or false")
    if include_cross_border_value not in {"true", "false"}:
        return None, error_response(400, "invalid_request", "include_cross_border must be true or false")
    return {
        "country": country,
        "postal_code": postal_code,
        "radius_km": int(radius_value),
        "order": order,
        "limit": int(limit_value),
        "offset": int(offset_value),
        "localities_only": localities_only_value == "true",
        "include_cross_border": include_cross_border_value == "true",
    }, None


def distance_km(first: dict[str, object], second: dict[str, object]) -> float:
    """Return the WGS84 ellipsoid geodesic distance in kilometres.

    GeographicLib accepts latitude then longitude in degrees and returns ``s12``
    in metres, so the public API continues returning kilometres.
    """
    inverse = Geodesic.WGS84.Inverse(
        float(first["latitude"]),
        float(first["longitude"]),
        float(second["latitude"]),
        float(second["longitude"]),
    )
    return float(inverse["s12"]) / 1000


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_RADIUS_KM"] = configured_max_radius_km()
    app.config["RATE_LIMIT_PER_MINUTE"] = configured_rate_limit_per_minute()
    app.config["TRUST_PROXY_HEADERS"] = configured_trusted_proxy_headers()
    rate_limiter = ClientRateLimiter(app.config["RATE_LIMIT_PER_MINUTE"])

    def client_ip() -> str:
        if app.config["TRUST_PROXY_HEADERS"]:
            forwarded_for = request.headers.get("X-Forwarded-For", "")
            forwarded_client = forwarded_for.split(",", 1)[0].strip()
            if forwarded_client:
                return forwarded_client
        return request.remote_addr or "unknown"

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/v1/postal-codes/nearby")
    def nearby():
        retry_after = rate_limiter.retry_after(client_ip())
        if retry_after is not None:
            return error_response(
                429,
                "rate_limited",
                "request rate limit exceeded; retry later",
                {"Retry-After": str(retry_after)},
            )
        query, query_error = parse_query(app.config["MAX_RADIUS_KM"])
        if query_error:
            return query_error
        assert query is not None

        by_postal_code, _, raw_grid = load_postal_codes()
        origin = by_postal_code.get((query["country"], query["postal_code"]))
        if origin is None:
            return error_response(404, "postal_code_not_found", "postal_code was not found for country")

        grid = load_postal_codes(LOCALITIES_DATA_FILE)[2] if query["localities_only"] else raw_grid

        minimum_latitude, maximum_latitude, minimum_longitude, maximum_longitude = bounding_box(
            origin, query["radius_km"]
        )
        results = []
        for candidate in grid_candidates(
            grid,
            minimum_latitude,
            maximum_latitude,
            minimum_longitude,
            maximum_longitude,
        ):
            if not query["include_cross_border"] and candidate["country"] != origin["country"]:
                continue
            calculated_distance = distance_km(origin, candidate)
            if calculated_distance <= query["radius_km"]:
                results.append({
                    "country": candidate["country"],
                    "postal_code": candidate["postal_code"],
                    "city_name": candidate["city_name"],
                    "distance_km": round(calculated_distance, 3),
                })
        if query["order"] == "asc":
            results.sort(key=lambda item: (item["distance_km"], item["country"], item["postal_code"]))
        else:
            results.sort(key=lambda item: (-item["distance_km"], item["country"], item["postal_code"]))

        total_results = len(results)
        offset = int(query["offset"])
        limit = int(query["limit"])
        page = results[offset : offset + limit]
        return jsonify(
            {
                "query": query,
                "results": page,
                "total_results": total_results,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total_results,
            }
        )

    return app


app = create_app()
