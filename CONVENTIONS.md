# Helianthus HA Integration – Conventions

## Home Assistant

- Use async (`async def`) and `aiohttp` for GraphQL.
- Use `DataUpdateCoordinator` for polling.
- Entity creation follows HA device registry best practices.

## mDNS / Zeroconf

- Use HA zeroconf discovery to prefill config flow.
- Service type: `_helianthus-graphql._tcp`.

## Testing

- Use `pytest`.
- Prefer unit tests with mocked GraphQL responses.

## Style

- Python 3.11
- Avoid heavy dependencies unless required by HA.
