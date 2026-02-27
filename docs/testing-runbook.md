# Testing Runbook

Copy/paste commands for reliable Phase 2 testing in `homeassistant-phyn`.

## Prerequisites

- Repos checked out (for local editable mode):
  - `../homeassistant-phyn`
  - `../aiophyn`
- Windows Python launcher available as `py`
- `uv` available via Python module:

```powershell
py -3.14 -m pip install --user uv
```

## Local test run (editable local `aiophyn`)

Use this when actively developing both repos together.

```powershell
cd C:\dev\homeassistant-phyn
$env:UV_PROJECT_ENVIRONMENT='.uvtest312'
$env:UV_NO_PROGRESS='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
py -3.14 -m uv run --python 3.12 --with-requirements requirements_test.txt pytest -p pytest_asyncio.plugin tests/test_fixture_statistics.py tests/test_fixture_statistics_importer.py tests/test_services_fixture_statistics.py -v
```

## Local test run (published `aiophyn` from PyPI)

Use this to validate installation behavior with released package dependencies.

```powershell
cd C:\dev\homeassistant-phyn
$env:UV_PROJECT_ENVIRONMENT='.uvtest312'
$env:UV_NO_PROGRESS='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
py -3.14 -m uv run --python 3.12 --with-requirements requirements_test_public.txt pytest -p pytest_asyncio.plugin tests/test_fixture_statistics.py tests/test_fixture_statistics_importer.py tests/test_services_fixture_statistics.py -v
```

## Run full local suite

```powershell
cd C:\dev\homeassistant-phyn
$env:UV_PROJECT_ENVIRONMENT='.uvtest312'
$env:UV_NO_PROGRESS='1'
py -3.14 -m uv run --python 3.12 --with-requirements requirements_test.txt pytest tests/ -v
```

## GitHub Actions run (no local environment required)

Workflow: `.github/workflows/phase2-tests.yml`

In Actions UI, run **Phase 2 Test Runner** with:

- `aiophyn_source`:
  - `repo` to test against a repo branch/ref
  - `pypi` to test against a published package version
- `test_scope`:
  - `phase2` for fixture statistics tests only
  - `all` for full suite

### Recommended inputs (active branch validation)

- `aiophyn_source=repo`
- `aiophyn_repo=jordanruthe/aiophyn`
- `aiophyn_ref=feature/fixture-usage`
- `test_scope=phase2`

### Recommended inputs (release validation)

- `aiophyn_source=pypi`
- `aiophyn_version=2026.2.1`
- `test_scope=phase2`

## Notes

- `requirements_test.txt` uses editable `-e ../aiophyn`.
- `requirements_test_public.txt` uses published `aiophyn>=2026.2.1`.
- If your local shell is noisy/slow with plugin autoload on Windows, keep `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and load `pytest_asyncio.plugin` explicitly as shown above.

## Troubleshooting

### `uv` is not recognized

Symptom:

- `uv : The term 'uv' is not recognized...`

Fix:

```powershell
py -3.14 -m pip install --user uv
py -3.14 -m uv --version
```

### `ciso8601` build errors on Windows

Symptom:

- Build fails with Visual C++ toolchain message when using Python 3.14

Fix:

- Force Python 3.12 in `uv run` commands:

```powershell
py -3.14 -m uv run --python 3.12 ...
```

### Resolver errors around Home Assistant test dependencies

Symptom:

- Unsatisfiable requirements for pytest / pytest-homeassistant-custom-component

Fix:

- Use the checked-in requirement files exactly as-is.
- Ensure `aiophyn` dependency range supports HA test stack (`paho-mqtt>=1.6.1,<3.0.0` in local `aiophyn`).

### Missing modules during collection (`psutil_home_assistant`, `fnv_hash_fast`)

Symptom:

- Import errors while loading `homeassistant.components.recorder`

Fix:

- Keep explicit packages in requirements files:
  - `psutil-home-assistant`
  - `fnv-hash-fast`

### `SocketBlockedError` on Windows during event loop setup

Symptom:

- `pytest_socket.SocketBlockedError: A test tried to use socket.socket`

Fix:

- Prefer plugin-minimal command for local Windows validation:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
py -3.14 -m uv run --python 3.12 --with-requirements requirements_test.txt pytest -p pytest_asyncio.plugin tests/test_fixture_statistics.py tests/test_fixture_statistics_importer.py tests/test_services_fixture_statistics.py -v
```

### Async tests are skipped in plugin-minimal mode

Symptom:

- `PytestUnhandledCoroutineWarning` and async tests show as skipped

Fix:

- Ensure `pytest_asyncio` plugin is explicitly loaded:
  - `-p pytest_asyncio.plugin`

## Known-good output

For the Phase 2 command in this runbook, a successful run should end with output similar to:

```text
collected 12 items
...
======================== 12 passed, 1 warning in ~4s ========================
```

If you do not see all 12 tests pass, compare your command/environment to the sections above (especially Python version and plugin flags).
