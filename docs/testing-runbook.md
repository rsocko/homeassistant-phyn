# Testing Runbook

Use Python 3.14 for the current Home Assistant test harness.

## Local tests with published aiophyn

From the integration checkout on Windows:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements_test.txt
.\.venv\Scripts\python.exe -m pytest tests -v
```

`requirements_test_public.txt` includes the same requirements, including
`aiophyn>=2026.9.1`. There is no implicit dependency on a sibling checkout.

To run only fixture usage and upstream compatibility coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_fixture_statistics.py tests\test_fixture_statistics_importer.py tests\test_services_fixture_statistics.py tests\test_upstream_compatibility.py -v
```

Home Assistant's test harness is primarily supported on Linux. If its platform
dependencies or socket fixtures fail on Windows, use the Linux Actions workflow;
do not downgrade to Python 3.12 or the old Home Assistant harness, which cannot
exercise the current `homeassistant.helpers.target` API.

## Testing a local aiophyn checkout

When developing both repositories, explicitly select a compatible local checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_test.txt -e ..\aiophyn
.\.venv\Scripts\python.exe -m pytest tests -v
```

The checkout must satisfy `aiophyn>=2026.9.1` and provide
`get_water_usage_events(device_id, from_ts, to_ts)` with millisecond timestamps.
The old `feature/fixture-usage` datetime signature is not compatible with this
updated integration.

## GitHub Actions

Run **Phase 2 Test Runner** (`.github/workflows/phase2-tests.yml`).

For released dependencies, use:

- `aiophyn_source=pypi`
- `aiophyn_version=2026.9.1`
- `test_scope=phase2` (fixture and upstream compatibility tests) or `all`

For library development, use:

- `aiophyn_source=repo`
- `aiophyn_repo=jordanruthe/aiophyn` (or your compatible fork)
- `aiophyn_ref=main` (or a compatible branch/tag/SHA)

Both paths use Python 3.14 and resolve the library together with the test
requirements. A selected version older than the integration's minimum should
fail dependency resolution rather than silently downgrade the runtime library.

## Upstream merge verification

The compatibility tests cover the published water-event API, retained fixture
service registration, boolean extended leak tests with Home Assistant's target
helper, and upstream's lifetime-consumption handling. No live Phyn credentials
are needed. They do not replace checking actual device behavior in Home Assistant.
