# Testing Runbook

## Baseline and release gate

Use **Linux and Python 3.14** (a Linux container or WSL is also suitable).
`requirements_test.txt` pins `pytest-homeassistant-custom-component==0.13.366`,
which selects Home Assistant **2026.9.3**. This is a tested development pairing,
not proof of support for the declared minimum HA 2026.6.4 or every later version.
Transitive dependencies are not fully locked; keep the resolver/provenance logs.

**Published aiophyn 2026.9.1 currently cannot resolve with this HA pairing.**
It requires `pycognito>=2022.8.0,<2023.0.0`, whereas HA's
`hass-nabucasa==2.7.0` requires `pycognito==2024.5.1`. The candidate library
corrects this to `pycognito>=2024.5.1,<2025`, but still reports version 2026.9.1.
Version output alone therefore cannot distinguish the candidate from PyPI.
Do not bypass resolution with `--no-deps`, downgrade HA, skip tests, or treat
candidate success as published compatibility. The normal release lane must
expose the actual failure until a compatible public release is approved.

Recorded final-candidate evidence:
[run 35686991026](https://github.com/rsocko/homeassistant-phyn/actions/runs/35686991026)
passed all **59 offline tests**, including **11 real Recorder/recovery cases**,
on Linux Python 3.14.7 / HA 2026.9.3 / harness 0.13.366. The integration checkout
was `379f3bffacce698e52503f3fa0740956e7ed1483`, runtime-equivalent to
`6e47ab8a2332b363804990edaedee5dca2b7387e` plus an isolated validation workflow.
The library was the **final remediation candidate**
`972f16c8fb0ede1a2f3365680a972f70c47b6fe7`, not the published distribution.
Joint resolution, `pip check` and archive/import provenance passed. This evidence
covers that exact runtime/library pair, not later workflow changes or a public
release. No release version or artifact has been selected by this development
workflow.

## Local published-dependency lane

Run these Bash commands from the integration checkout in a fresh disposable
environment, separate from the editable lane:

```bash
set -euo pipefail
PUBLIC_ENV="$(mktemp -d)/venv"
python3.14 -m venv "$PUBLIC_ENV"
source "$PUBLIC_ENV/bin/activate"
python -m pip install -r requirements_test_public.txt "aiophyn==2026.9.1"
python -m pip check
python - <<'PY'
from importlib.metadata import distribution
from pathlib import Path
import aiophyn

for name in ("homeassistant", "pytest-homeassistant-custom-component", "aiophyn"):
    package = distribution(name)
    print(f"{name}=={package.version}")
    assert package.read_text("direct_url.json") is None, name
package = distribution("aiophyn")
assert package.version == "2026.9.1"
imported = Path(aiophyn.__file__).resolve()
print(f"aiophyn import={imported}")
assert imported == Path(package.locate_file("aiophyn/__init__.py")).resolve()
PY
python -m pip freeze
python -m pytest tests/ -v --disable-socket --allow-unix-socket
```

The install currently fails for the conflict above, before tests can run.
`requirements_test_public.txt` remains an include of `requirements_test.txt`;
the explicit version selects the published baseline, not a sibling checkout.

## Editable integration/library loop

Use a separate, clean Linux environment. Set `AIOPHYN_CHECKOUT` to your explicit
local library checkout before running this recipe from the integration root.
Use a reviewed clean full commit for final verification; uncommitted library
edits are useful only for the local development loop.

```bash
set -euo pipefail
: "${AIOPHYN_CHECKOUT:?Set an absolute path to the selected aiophyn checkout}"
AIOPHYN_CHECKOUT="$(realpath "$AIOPHYN_CHECKOUT")"
export AIOPHYN_CHECKOUT
DEV_ENV="$(mktemp -d)/venv"
python3.14 -m venv "$DEV_ENV"
source "$DEV_ENV/bin/activate"
git rev-parse HEAD
git status --short
git -C "$AIOPHYN_CHECKOUT" rev-parse HEAD
git -C "$AIOPHYN_CHECKOUT" status --short
python --version
python -m pip install -r requirements_test.txt -e "$AIOPHYN_CHECKOUT"
python -m pip check
python - <<'PY'
from importlib.metadata import distribution
import json
import os
from pathlib import Path
import aiophyn

checkout = Path(os.environ["AIOPHYN_CHECKOUT"]).resolve()
package = distribution("aiophyn")
source = json.loads(package.read_text("direct_url.json") or "{}")
imported = Path(aiophyn.__file__).resolve()
print(f"aiophyn=={package.version}\nimport={imported}\ndirect_url={source}")
assert source.get("url") == checkout.as_uri(), source
assert source.get("dir_info", {}).get("editable") is True, source
assert imported.is_relative_to(checkout), imported
for name, expected in (
    ("homeassistant", "2026.9.3"),
    ("pytest-homeassistant-custom-component", "0.13.366"),
):
    version = distribution(name).version
    print(f"{name}=={version}")
    assert version == expected
PY
python -m pip freeze
python -m pytest tests/ -v --disable-socket --allow-unix-socket
```

The editable candidate **must participate in the same initial pip transaction
as the harness and requirements**. Installing incompatible public dependencies
first and overriding afterward fails before the override. Keep the explicit
candidate in subsequent resolution commands; do not reinstall the broad
requirements alone. Recheck `pip check` and provenance after environment changes.
A verified immutable full-commit archive can replace the editable argument in
that same transaction, but must be checked against its exact `direct_url.json`
archive URL/hash and installed import path rather than editable-directory rules.

The integration calls `get_water_usage_events(device_id, from_ts=..., to_ts=...)`
with **epoch milliseconds**. A candidate may also offer datetime bounds; that
does not remove the required epoch-ms contract. Autoshutoff uses upstream's
spelling `get_autoshuftoff_status`; there is no corrected-name alias requirement.
Tests replace network boundaries, not the actual library method under contract
test. No credentials, live Phyn calls, or valve operations are required.

For the bounded fixture/compatibility selector (also used by `phase2`):

```bash
python -m pytest \
  tests/test_fixture_statistics.py \
  tests/test_fixture_statistics_importer.py \
  tests/test_services_fixture_statistics.py \
  tests/test_upstream_compatibility.py \
  tests/test_fixture_statistics_recorder.py \
  tests/test_fixture_statistics_lifecycle.py \
  -v --disable-socket --allow-unix-socket
```

Run the full suite for final pairing. Optional coverage uses the same interpreter:
`python -m pytest tests/ --cov=custom_components.phyn --cov-report=term-missing --disable-socket --allow-unix-socket`.
The HA harness is Linux-first. Windows package/platform or socket-fixture
failures are not compatibility results; use WSL/Linux or the authorized Actions
route instead of an older harness, dependency bypass, or disabled TLS checks.

## Isolated HA Core development (not deployment)

Only after offline checks, and with separate authorization for live operation,
use the **same activated environment** as the editable loop. This is a
disposable Linux HA Core config, never pip on an HA OS host or manual changes
to a production HA environment. From the integration root:

```bash
DEV_CONFIG="$(mktemp -d)"
mkdir -p "$DEV_CONFIG/custom_components"
ln -s "$PWD/custom_components/phyn" "$DEV_CONFIG/custom_components/phyn"
# Inspect the selective dependency-skip option without starting HA:
hass --help
# Explicit manual startup only; this is not part of offline validation:
hass --config "$DEV_CONFIG" --skip-pip-packages aiophyn
```

`python -m homeassistant` is the equivalent entry point. Skip **only aiophyn**,
not all dependencies with `--skip-pip`. The selective flag prevents HA's manifest
installer replacing the verified editable library. Do not edit the manifest to
a local path. Do not configure a real account or trigger controls as part of
automated development tests.

## GitHub Actions and provenance

Normal **Validation** (`tests_nocoverage.yml`) preserves hassfest, HACS,
translation consistency and mypy checks. Its **Full offline suite with published
aiophyn** job jointly resolves the explicit public baseline and HA harness,
checks dependency/import provenance and runs all tests if resolution succeeds.
Its current resolver failure is intentional visibility of a real incompatibility,
not a waived/allowed failure. Structural validators and missing-import mypy do
not prove runtime compatibility.

**Phase 2 Test Runner** (`phase2-tests.yml`) retains two explicit sources:

| Input | Published lane | Candidate lane |
| --- | --- | --- |
| `aiophyn_source` | `pypi` (default) | `repo` |
| `aiophyn_version` | Numeric dotted published version; default `2026.9.1` | Ignored |
| `aiophyn_sha` | Ignored | Required full 40-hex commit in **rsocko/aiophyn** |
| `test_scope` | `all` (default) or `phase2` | `all` (default) or `phase2` |

There is no candidate default, arbitrary repository, moving branch/tag or
abbreviated SHA. Repo mode normalizes hexadecimal case, verifies checkout HEAD
and clean state, and asserts editable `direct_url.json` and import-directory
provenance. Both lanes run `pip check` and log integration/library provenance,
Python, HA/harness versions and the resolved package list. Both run the same
applicable tests with network sockets blocked (local Unix sockets allowed).
There are no capability skips or success-shaped candidate fallbacks.

**Dispatch limitation:** GitHub cannot run this `workflow_dispatch` workflow
until it is registered on the default branch. This development layer does not
register it or authorize a default-branch change. The current authorized route
is the cloud owner's isolated `rsocko-phyn-cloud-validation` branch and its
push-triggered `fixture-validation.yml`. Record exact checked-out commits and
whether it ran equivalent steps: that is not proof that manual dispatch itself
was exercised. Final library changes and tooling changes require a new paired
run, not reuse of an earlier green result.

## Fixture history, corrections and recovery

The runtime retains compact **accepted contributions for all imported history**,
with no automatic expiry. Storage growth is intentional. Updates are serialized
per device and use each device-scoped event ID's **last locally observed accepted
contribution**, not a server revision number or proof of the server's newest
truth. A stale later observation can still supersede local evidence.

Missing events in a response are **never deletions**. Server result caps,
pagination/completeness, revision ordering, boundary semantics and event-ID
stability remain unknown. The services' `days` range of **1-365**, or explicit
`start_datetime`/`end_datetime`, selects a request window; it does not guarantee
complete returned history. Labels and API `fixture_id` categories are not proven
identities for distinct physical fixtures in a household. Statistics series
are label/category-based per device, not a household inventory.

`phyn.import_fixture_statistics` and `phyn.reload_fixture_statistics` reconcile
observations. Corrections update old/new label rows and downstream cumulative
sums. `force_reimport: true` replays the observations without clearing whole
series; omission still preserves history. Use `dry_run: true` to preview a
bounded request before writes. Preview does not write or recover a pending
operation and reports an error when earlier work is pending.

Accepted evidence and checkpoints commit only after absolute Recorder rows and
metadata have been read back and verified. An interrupted/pending import retains
its journal for replay on a subsequent non-dry-run import; failed verification
does not count as success. This is not a general repair or rollback system.

There is **no automatic migration or clearing** of unsupported legacy/developer
state. Ambiguous state, orphan fixture Recorder history, or a mismatch with
accepted evidence produces a persistent notice and pauses **fixture-statistics
writes only**; normal sensors and controls continue. Preserve the state/history
and report the notice for investigation. Do not delete `.storage` files or
Recorder history to bypass it, and do not assume an older integration can safely
read the new state.

## Gated HACS development artifact and stable release

The editable loop is not a HACS install. **HACS installs the integration; HA
installs the Python dependency declared by its manifest.** Installing the fork
does not automatically select the aiophyn fork. The README's upstream HACS
instructions do not install this fork's fixture additions. Both integrations
use domain `phyn`; do not install upstream and this fork side by side.

A future opt-in development release needs a uniquely versioned library build,
final paired offline validation, clean out-of-tree artifact installation and an
approved **public immutable** artifact (prefer a wheel/sdist URL with a verified
SHA-256 hash; a full-commit archive only after build/install verification).
Do not use moving refs, replaceable unhashed assets, private Actions artifacts,
editable paths or developer dependency-skip flags for rollout. No artifact,
version or manifest replacement is selected here. The release owner must
validate HA dependency installation and HACS/hassfest acceptance before rollout.

Stable order: publish the approved compatible aiophyn release first, update the
integration's manifest to its approved exact PyPI pin and align the test
baseline, then verify clean installation/full tests before releasing the
integration. A green candidate with version 2026.9.1 does not clear the existing
public 2026.9.1 gate. Release, deployment and live-device verification require
separate approval.
