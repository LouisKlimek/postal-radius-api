# DACH Postal Radius API

Offline Flask API for finding German, Austrian, and Swiss postal codes within a geodesic radius. The container bundles its dataset and makes no runtime calls to external geodata services.

## Run with Docker

Build the image:

```bash
docker build -t louis-klimek/postal-radius-api .
```

Start it on a non-default port:

```bash
docker run --rm -e PORT=9090 -p 9090:9090 louis-klimek/postal-radius-api
```

Health check:

```bash
curl http://localhost:9090/health
# {"status":"ok"}
```

## API contract

### `GET /v1/postal-codes/nearby`

Required query parameters:

- `country`: `DE`, `AT`, or `CH` (case-insensitive; normalized to upper-case)
- `postal_code`: postal code as a string; leading zeroes are retained
- `radius_km`: positive integer radius in kilometres

Optional query parameter:

- `order`: `asc` (default) or `desc`

Example:

```bash
curl --get 'http://localhost:9090/v1/postal-codes/nearby' \
  --data-urlencode country=DE \
  --data-urlencode postal_code=01067 \
  --data-urlencode radius_km=100 \
  --data-urlencode order=asc
```

Successful responses contain normalized input under `query` and ordered `results`. Every result contains `country`, `postal_code`, `city_name`, and `distance_km` (rounded to three decimal places). Distances use the Haversine great-circle calculation with the IUGG mean Earth radius (6,371.0088 km). Results outside the requested radius are omitted.

Invalid parameters return HTTP 400 with:

```json
{"error":{"code":"invalid_request","message":"..."}}
```

An unknown country/postal-code pair returns HTTP 404 with:

```json
{"error":{"code":"postal_code_not_found","message":"postal_code was not found for country"}}
```

## Data

`data/dach_postal_centroids_geonames_2026-08-03.csv` is a versioned, image-local DACH postal-code centroid dataset generated from the GeoNames postal-code export for DE, AT, and CH, downloaded on 2026-08-03. GeoNames data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution: [GeoNames](https://www.geonames.org/).

The GeoNames export can contain more than one row for a country/postal-code pair. During generation, this service deterministically selects the lexicographically smallest tuple `(city_name, latitude, longitude)` for that pair. Therefore the selected `city_name` is stable and documented, rather than dependent on source-file ordering.

### Update data

Download `https://download.geonames.org/export/zip/DE.zip`, `AT.zip`, and `CH.zip`; read each tab-separated `<COUNTRY>.txt`; group by `(country, postal_code)`; choose the smallest `(place_name, latitude, longitude)` tuple; then write the resulting CSV with the same header and a new date-stamped filename. Update `DATA_FILE` in `app.py`, this README's version/date, and run the tests before building an image.

## Local development and tests

```bash
uv venv .venv
uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
```
