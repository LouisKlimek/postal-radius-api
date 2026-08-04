import app as postal_api
from app import create_app


def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_healthcheck_returns_service_status():
    response = client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def valid_nearby_query():
    return {"country": "DE", "postal_code": "01067", "radius_km": "1"}


def test_nearby_rate_limit_returns_stable_json_and_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    test_client = client()

    assert test_client.get("/v1/postal-codes/nearby", query_string=valid_nearby_query()).status_code == 200
    assert test_client.get("/v1/postal-codes/nearby", query_string=valid_nearby_query()).status_code == 200
    limited = test_client.get("/v1/postal-codes/nearby", query_string=valid_nearby_query())

    assert limited.status_code == 429
    assert limited.get_json() == {
        "error": {
            "code": "rate_limited",
            "message": "request rate limit exceeded; retry later",
        }
    }
    assert int(limited.headers["Retry-After"]) >= 1


def test_health_is_exempt_from_nearby_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    test_client = client()

    assert test_client.get("/v1/postal-codes/nearby", query_string=valid_nearby_query()).status_code == 200
    assert test_client.get("/health").status_code == 200
    assert test_client.get("/health").status_code == 200
    assert test_client.get("/v1/postal-codes/nearby", query_string=valid_nearby_query()).status_code == 429


def test_rate_limit_configuration_and_proxy_trust_boundary(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    untrusted_client = client()

    assert untrusted_client.get(
        "/v1/postal-codes/nearby", query_string=valid_nearby_query(), headers={"X-Forwarded-For": "198.51.100.1"}
    ).status_code == 200
    assert untrusted_client.get(
        "/v1/postal-codes/nearby", query_string=valid_nearby_query(), headers={"X-Forwarded-For": "198.51.100.2"}
    ).status_code == 429

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    trusted_client = client()
    assert trusted_client.get(
        "/v1/postal-codes/nearby", query_string=valid_nearby_query(), headers={"X-Forwarded-For": "198.51.100.1"}
    ).status_code == 200
    assert trusted_client.get(
        "/v1/postal-codes/nearby", query_string=valid_nearby_query(), headers={"X-Forwarded-For": "198.51.100.2"}
    ).status_code == 200


def test_nearby_preserves_leading_zero_and_returns_distance_zero():
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={"country": "DE", "postal_code": "01067", "radius_km": "1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["query"] == {
        "country": "DE",
        "postal_code": "01067",
        "radius_km": 1,
        "order": "asc",
        "limit": 1000,
        "offset": 0,
        "localities_only": True,
    }
    assert payload["results"][0]["postal_code"] == "01067"
    assert payload["results"][0]["city_name"] == "Dresden"
    assert payload["results"][0]["distance_km"] == 0.0


def test_nearby_honors_radius_boundary_and_descending_order():
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={
            "country": "DE",
            "postal_code": "01067",
            "radius_km": "100",
            "order": "desc",
        },
    )

    assert response.status_code == 200
    results = response.get_json()["results"]
    distances = [item["distance_km"] for item in results]
    assert distances == sorted(distances, reverse=True)
    assert all(distance <= 100 for distance in distances)
    assert any(item["postal_code"] == "01067" for item in results)


def test_nearby_rejects_invalid_input_and_unknown_postal_code():
    invalid_cases = [
        {"country": "XX", "postal_code": "01067", "radius_km": "1"},
        {"country": "DE", "postal_code": "01067", "radius_km": "0"},
        {"country": "DE", "postal_code": "01067", "radius_km": "1", "order": "up"},
        {"country": "DE", "postal_code": "01067", "radius_km": "1", "localities_only": "yes"},
    ]
    for query in invalid_cases:
        invalid = client().get("/v1/postal-codes/nearby", query_string=query)
        assert invalid.status_code == 400
        assert invalid.get_json()["error"]["code"] == "invalid_request"

    unknown = client().get(
        "/v1/postal-codes/nearby",
        query_string={"country": "DE", "postal_code": "99999", "radius_km": "1"},
    )
    assert unknown.status_code == 404
    assert unknown.get_json()["error"]["code"] == "postal_code_not_found"


def test_nearby_rejects_radius_over_configured_maximum(monkeypatch):
    monkeypatch.setenv("MAX_RADIUS_KM", "25")
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={"country": "DE", "postal_code": "01067", "radius_km": "26"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": {
            "code": "invalid_request",
            "message": "radius_km must not exceed MAX_RADIUS_KM (25)",
        }
    }


def test_nearby_rejects_invalid_limit_and_offset():
    base_query = {"country": "DE", "postal_code": "01067", "radius_km": "100"}
    invalid_cases = (
        ({**base_query, "limit": "0"}, "limit must be an integer between 1 and 1000"),
        ({**base_query, "limit": "1001"}, "limit must be an integer between 1 and 1000"),
        ({**base_query, "limit": "one"}, "limit must be an integer between 1 and 1000"),
        ({**base_query, "offset": "-1"}, "offset must be a non-negative integer"),
        ({**base_query, "offset": "one"}, "offset must be a non-negative integer"),
    )
    for query, message in invalid_cases:
        response = client().get("/v1/postal-codes/nearby", query_string=query)
        assert response.status_code == 400
        assert response.get_json() == {"error": {"code": "invalid_request", "message": message}}


def brute_force_results(country: str, postal_code: str, radius_km: int, localities_only: bool = True):
    by_postal_code, _, _ = postal_api.load_postal_codes()
    _, all_postal_codes, _ = postal_api.load_postal_codes(
        postal_api.LOCALITIES_DATA_FILE if localities_only else postal_api.RAW_DATA_FILE
    )
    origin = by_postal_code[(country, postal_code)]
    results = []
    for candidate in all_postal_codes:
        distance = postal_api.distance_km(origin, candidate)
        if distance <= radius_km:
            results.append(
                {
                    "country": candidate["country"],
                    "postal_code": candidate["postal_code"],
                    "city_name": candidate["city_name"],
                    "distance_km": round(distance, 3),
                }
            )
    return sorted(results, key=lambda item: (item["distance_km"], item["country"], item["postal_code"]))


def test_indexed_results_match_brute_force_for_dach_small_and_large_radii():
    for country, postal_code, radius_km in (
        ("DE", "01067", 3),
        ("DE", "01067", 500),
        ("AT", "1000", 3),
        ("AT", "1000", 500),
        ("CH", "1000", 3),
        ("CH", "1000", 500),
    ):
        response = client().get(
            "/v1/postal-codes/nearby",
            query_string={"country": country, "postal_code": postal_code, "radius_km": radius_km},
        )

        assert response.status_code == 200
        payload = response.get_json()
        expected = brute_force_results(country, postal_code, radius_km)
        assert payload["results"] == expected[:1000]
        assert payload["total_results"] == len(expected)
        assert payload["has_more"] is (len(expected) > 1000)


def test_index_skips_haversine_for_small_radius_non_candidates(monkeypatch):
    _, all_postal_codes, _ = postal_api.load_postal_codes()
    calls = 0
    original_distance = postal_api.distance_km

    def count_distance(first, second):
        nonlocal calls
        calls += 1
        return original_distance(first, second)

    monkeypatch.setattr(postal_api, "distance_km", count_distance)
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={"country": "DE", "postal_code": "01067", "radius_km": 3},
    )

    assert response.status_code == 200
    assert calls < len(all_postal_codes)


def test_nearby_pagination_returns_complete_non_overlapping_pages():
    base_query = {"country": "DE", "postal_code": "01067", "radius_km": "100", "limit": "7"}
    first = client().get("/v1/postal-codes/nearby", query_string=base_query).get_json()
    assert first["limit"] == 7
    assert first["offset"] == 0
    assert first["has_more"] is True

    pages = []
    for offset in range(0, first["total_results"], first["limit"]):
        payload = client().get(
            "/v1/postal-codes/nearby", query_string={**base_query, "offset": str(offset)}
        ).get_json()
        pages.extend(payload["results"])
        assert payload["offset"] == offset
        assert payload["has_more"] is (offset + first["limit"] < first["total_results"])

    assert len(pages) == first["total_results"]
    assert len({(item["country"], item["postal_code"]) for item in pages}) == len(pages)
    assert pages == brute_force_results("DE", "01067", 100)


def test_nearby_descending_order_has_stable_country_postal_tiebreaker():
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={"country": "DE", "postal_code": "01067", "radius_km": "100", "order": "desc", "limit": "1000"},
    )

    results = response.get_json()["results"]
    assert results == sorted(results, key=lambda item: (-item["distance_km"], item["country"], item["postal_code"]))


def test_nearby_localities_only_defaults_to_openplz_locality_snapshot():
    query = {"country": "DE", "postal_code": "76107", "radius_km": "1"}
    default_response = client().get("/v1/postal-codes/nearby", query_string=query)
    explicit_true_response = client().get(
        "/v1/postal-codes/nearby", query_string={**query, "localities_only": "true"}
    )
    raw_response = client().get(
        "/v1/postal-codes/nearby", query_string={**query, "localities_only": "false"}
    )

    assert default_response.status_code == explicit_true_response.status_code == raw_response.status_code == 200
    assert default_response.get_json() == explicit_true_response.get_json()
    assert default_response.get_json()["query"]["localities_only"] is True
    assert raw_response.get_json()["query"]["localities_only"] is False
    assert all(result["postal_code"] != "76107" for result in default_response.get_json()["results"])
    assert any(result["postal_code"] == "76107" for result in raw_response.get_json()["results"])
    assert raw_response.get_json()["results"] == brute_force_results("DE", "76107", 1, localities_only=False)


def test_nearby_rejects_non_boolean_localities_only():
    response = client().get(
        "/v1/postal-codes/nearby",
        query_string={**valid_nearby_query(), "localities_only": "1"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": {"code": "invalid_request", "message": "localities_only must be true or false"}
    }
