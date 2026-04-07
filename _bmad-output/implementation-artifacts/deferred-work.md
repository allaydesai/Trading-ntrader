# Deferred Work

## Deferred from: code review of 1-1-catalog-foundation-and-configuration (2026-04-06)

- W1: Async upsert TOCTOU race — SELECT then UPDATE without `FOR UPDATE` lock in `CatalogInstrumentRepository.upsert()`. Unique constraint catches collisions; acceptable for sequential import pipeline. Revisit if concurrent web API upserts are added.
- W2: `CatalogConfig.name` field redundant with dict key in `CatalogSettings.catalogs` — no validation they match. Low risk but could cause confusion.
- W3: `CatalogManager` is general-purpose catalog resolver but lives under `src/services/firstrate/`. Can relocate to `src/services/` if additional non-FirstRate catalogs are added.
