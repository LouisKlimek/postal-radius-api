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

Optional query parameters:

- `order`: `asc` (default) or `desc`
- `limit`: positive integer number of records to return (default and maximum: `1000`)
- `offset`: zero-based number of matching records to skip (default: `0`)

`MAX_RADIUS_KM` caps accepted radius values. It defaults to `500`; set it when starting the service to use a smaller operational bound, for example `MAX_RADIUS_KM=250 gunicorn --bind :9090 app:app`.

Example:

```bash
curl --get 'http://localhost:9090/v1/postal-codes/nearby' \
  --data-urlencode country=DE \
  --data-urlencode postal_code=01067 \
  --data-urlencode radius_km=100 \
  --data-urlencode order=asc
```

Successful responses contain normalized input under `query` and ordered `results`. Every result contains `country`, `postal_code`, `city_name`, and `distance_km` (rounded to three decimal places). Distances use the Haversine great-circle calculation with the IUGG mean Earth radius (6,371.0088 km). Results outside the requested radius are omitted.

### Pagination compatibility

`v1` successful responses now include `total_results`, `limit`, `offset`, and `has_more`. Existing clients still receive the same `query` and `results` fields, but `results` is capped at the default `limit` of 1,000. Clients that need every match must follow pages using the returned metadata. Results are ordered by distance, then by `country` and `postal_code` for stable pagination; `desc` reverses distance while retaining the country/postal-code tie-breaker.

Fetch the first page and then its following page:

```bash
curl --get 'http://localhost:9090/v1/postal-codes/nearby' \
  --data-urlencode country=DE \
  --data-urlencode postal_code=01067 \
  --data-urlencode radius_km=100 \
  --data-urlencode limit=100 \
  --data-urlencode offset=0

curl --get 'http://localhost:9090/v1/postal-codes/nearby' \
  --data-urlencode country=DE \
  --data-urlencode postal_code=01067 \
  --data-urlencode radius_km=100 \
  --data-urlencode limit=100 \
  --data-urlencode offset=100
```

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

### Runtime data lifecycle and in-memory spatial index

The versioned GeoNames DACH CSV is copied into the Docker image and remains the canonical runtime dataset. Container startup does not call an external Geo API, open a database connection, import the CSV into another store, or run a migration. The service instead reads the image-local CSV on its first data use.

That first load parses the CSV and deterministically assigns every canonical record to a 1° latitude/longitude grid cell, retaining the resulting spatial index and postal-code lookup map in memory for later requests. Gunicorn workers are separate processes, so each worker builds and retains its own cache on first use. The cache is rebuilt only when a worker first loads a newly deployed CSV; there is no separate index artifact or migration. To update data, replace the versioned CSV and update `DATA_FILE` as described below, then rebuild/restart the image.

For a nearby request, the service derives a latitude/longitude bounding box from the requested radius, enumerates only intersecting grid cells, then applies a record-level bounding-box filter before running Haversine. Haversine remains the final inclusion decision, so the response preserves exact-radius semantics, city names, and ordering. Longitude extent uses the spherical bound and a pole-safe guard; all supported DACH locations are within its normal range.

### Why this service has no database

The DACH dataset is static and modest in size (about 16.7k canonical records), so a single-container, offline deployment can use the in-memory spatial index without database provisioning, migrations, network access, or runtime operational overhead. This keeps nearby lookups efficient while retaining the versioned CSV as the source of truth.

This trade-off is deliberate rather than permanent: a materially larger or dynamic dataset, or multi-replica operation that needs global querying or rate coordination, may justify a dedicated spatial datastore later.

### Update data

Download `https://download.geonames.org/export/zip/DE.zip`, `AT.zip`, and `CH.zip`; read each tab-separated `<COUNTRY>.txt`; group by `(country, postal_code)`; choose the smallest `(place_name, latitude, longitude)` tuple; then write the resulting CSV with the same header and a new date-stamped filename. Update `DATA_FILE` in `app.py`, this README's version/date, and run the tests before building an image.

## Local development and tests

```bash
uv venv .venv
uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
```
