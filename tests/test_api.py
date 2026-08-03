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


def brute_force_results(country: str, postal_code: str, radius_km: int):
    by_postal_code, all_postal_codes, _ = postal_api.load_postal_codes()
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
        assert response.get_json()["results"] == brute_force_results(country, postal_code, radius_km)


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
