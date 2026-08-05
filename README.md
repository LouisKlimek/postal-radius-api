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
- `localities_only`: `true` (default) returns only OpenPLZ `Locality` entities with an exact coordinate enrichment; `false` returns the complete prior GeoNames raw dataset, including delivery, company, and authority labels. Only the literal boolean values `true` and `false` are accepted (case-insensitive).
- `include_cross_border`: `false` (default) returns only records whose country metadata matches the resolved origin postal code. Set it explicitly to `true` to include postal codes from other countries within the radius. Only the literal boolean values `true` and `false` are accepted (case-insensitive).

`MAX_RADIUS_KM` caps accepted radius values. It defaults to `500`; set it when starting the service to use a smaller operational bound, for example `MAX_RADIUS_KM=250 gunicorn --bind :9090 app:app`.

### Request rate limiting

`/v1/postal-codes/nearby` is protected by an in-process, per-client-IP fixed-window limit. `RATE_LIMIT_PER_MINUTE` controls the limit and defaults to `120` requests per minute. Requests above the limit receive HTTP `429` with the stable response body:

```json
{"error":{"code":"rate_limited","message":"request rate limit exceeded; retry later"}}
```

The response also includes `Retry-After` (whole seconds until the current window resets). `/health` is not rate limited.

By default the limiter uses the direct peer address and ignores forwarded client-IP headers such as `X-Forwarded-For`. Set `TRUST_PROXY_HEADERS=true` only when the application is deployed behind a proxy that strips client-supplied forwarding headers and sets them itself; when enabled, the first `X-Forwarded-For` value is used as the client IP.

This protection is intentionally limited to one application process/container. It does not provide a shared global quota across replicas, processes, restarts, or hosts. Multi-replica global rate limiting requires a shared external store or a gateway/load-balancer policy.

Example:

```bash
curl --get 'http://localhost:9090/v1/postal-codes/nearby' \
  --data-urlencode country=DE \
  --data-urlencode postal_code=01067 \
  --data-urlencode radius_km=100 \
  --data-urlencode order=asc
```

Successful responses contain normalized input under `query` and ordered `results`. Every result contains `country`, `postal_code`, `city_name`, and `distance_km` (rounded to three decimal places). Distances use [GeographicLib](https://geographiclib.sourceforge.io/)’s WGS84 ellipsoid inverse geodesic calculation: input coordinates are latitude/longitude degrees, GeographicLib returns metres, and the API returns kilometres. Results outside the requested radius are omitted.

`localities_only` and `include_cross_border` are reflected as booleans in the normalized `query` object. The lookup postal code is always resolved from the raw GeoNames lookup so a caller can search around a delivery postal code; in `localities_only=true` mode, only result candidates come from the OpenPLZ locality snapshot. The country restriction uses the resolved origin's dataset metadata, not a hard-coded country assumption.

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

`data/dach_postal_centroids_geonames_2026-08-03.csv` is the versioned, image-local DACH postal-code centroid dataset generated from the GeoNames postal-code export for DE, AT, and CH, downloaded on 2026-08-03. GeoNames data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution: [GeoNames](https://www.geonames.org/). It remains the raw dataset used by `localities_only=false` and for locating every request origin.

`data/dach_localities_openplz_2026-08-04.csv` is the versioned `localities_only=true` candidate snapshot. Its classification source is the public OpenPLZ `/de/Localities`, `/at/Localities`, and `/ch/Localities` endpoints. The source-native classification is the `Locality` entity itself: each entity carries `postalCode`, `name`, and administrative-unit metadata. No GeoNames row is classified by its name and no local maintainership list or heuristic is used.

OpenPLZ's API OpenAPI document links to the project license, and the [OpenPLZ data repository](https://github.com/openpotato/openplzapi.data) is licensed under [ODbL-1.0](https://github.com/openpotato/openplzapi.data/blob/main/LICENSE). This snapshot contains only OpenPLZ `Locality` entities. Coordinates are retained only when an already accepted `(country, postalCode, name)` tuple exactly equals a GeoNames `(country, postal_code, city_name)` tuple; this is technical coordinate enrichment, not classification. At the 2026-08-04 snapshot, 37,603 OpenPLZ entities yielded 12,741 country/postal-code records with exact coordinates. Localities without that exact enrichment are intentionally absent from localities-only radius results.

The GeoNames export can contain more than one row for a country/postal-code pair. During generation, this service deterministically selects the lexicographically smallest tuple `(city_name, latitude, longitude)` for that pair. Therefore the selected `city_name` is stable and documented, rather than dependent on source-file ordering.

### Runtime data lifecycle and in-memory spatial index

Both versioned CSVs are copied into the Docker image. Container startup does not call an external Geo API, open a database connection, import the CSV into another store, or run a migration. The service instead reads the image-local CSVs on their first data use.

That first load parses each CSV and deterministically assigns every record to a 1° latitude/longitude grid cell, retaining the resulting spatial index and postal-code lookup map in memory for later requests. Gunicorn workers are separate processes, so each worker builds and retains its own cache on first use. The cache is rebuilt only when a worker first loads a newly deployed CSV; there is no separate index artifact or migration. To update data, replace the versioned CSVs and update the filename constants as described below, then rebuild/restart the image.

For a nearby request, the service derives a latitude/longitude bounding box from the requested radius, enumerates only intersecting grid cells, then applies a record-level bounding-box filter before running the WGS84 inverse geodesic calculation. The geodesic remains the final inclusion decision, so the response preserves exact-radius semantics, city names, and ordering. Longitude extent uses the spherical bound and a pole-safe guard; all supported DACH locations are within its normal range.

### Why this service has no database

The DACH dataset is static and modest in size (about 16.7k canonical records), so a single-container, offline deployment can use the in-memory spatial index without database provisioning, migrations, network access, or runtime operational overhead. This keeps nearby lookups efficient while retaining the versioned CSV as the source of truth.

This trade-off is deliberate rather than permanent: a materially larger or dynamic dataset, or multi-replica operation that needs global querying or rate coordination, may justify a dedicated spatial datastore later.

### Update data

For the raw GeoNames update, download `https://download.geonames.org/export/zip/DE.zip`, `AT.zip`, and `CH.zip`; read each tab-separated `<COUNTRY>.txt`; group by `(country, postal_code)`; choose the smallest `(place_name, latitude, longitude)` tuple; then write the raw CSV with the same header and a new date-stamped filename.

For the locality snapshot, after creating the raw CSV run:

```bash
python3 scripts/build_openplz_localities_snapshot.py \
  data/<raw-geonames-file>.csv \
  data/dach_localities_openplz_<YYYY-MM-DD>.csv
```

The script pages the three OpenPLZ `Localities` endpoints using postal-code first digits, accepts only returned `Locality` entities, and writes only exact tuple coordinate enrichments. Update `RAW_DATA_FILE` and `LOCALITIES_DATA_FILE` in `app.py`, this README's dates/counts, and run the tests before building an image.

## Local development and tests

```bash
uv venv .venv
uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
```
