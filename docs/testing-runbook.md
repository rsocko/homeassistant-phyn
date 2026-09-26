# Testing Runbook

## Baseline and release gate

Use **Linux and Python 3.14** (a Linux container or WSL is also suitable).
`requirements_test.txt` pins `pytest-homeassistant-custom-component==0.13.366`,
which selects Home Assistant **2026.9.3**. The HACS minimum now conservatively
matches that baseline; `homeassistant` is not a supported integration manifest
field and is omitted there. This is not proof that older releases are
broken or that every later version has been tested.
Transitive dependencies are not fully locked; keep the resolver/provenance logs.

**Published aiophyn 2026.9.1 currently cannot resolve with this HA pairing.**
It requires `pycognito>=2022.8.0,<2023.0.0`, whereas HA's
`hass-nabucasa==2.7.0` requires `pycognito==2024.5.1`. The candidate library
corrects this to `pycognito>=2024.5.1,<2025`. Earlier candidate commits still
reported version 2026.9.1, so their version alone cannot distinguish them from
PyPI. The HACS development artifact is separately versioned **2026.9.2.dev1**.
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
release. Current HACS development packaging is described below; those older
results do not establish its installation behavior.

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
register it or authorize a default-branch change. The former isolated validation
branch was consolidated and deleted. The authorized route is now
`fixture-validation.yml` on pushes to `feature/fixture-usage`, using immutable
aiophyn `b187b745eeb9e0dfd9ba7bf86bcf706a50e41280` for editable verification,
and the exact manifest wheel for artifact verification. It runs offline artifact
and editable suites, without Phyn credentials or live-device calls. Validation covers
only the selected commit, not future library changes. Record exact checked-out commits and
whether it ran equivalent steps: that is not proof that manual dispatch itself
was exercised. Final library changes and tooling changes require a new paired
run, not reuse of an earlier green result.

## Fixture history, corrections and recovery

Attribution follows the [consumer policy and app review guidance](../README.md#fixture-attribution-and-review).
The synthetic vectors in `tests/fixtures/attribution_cases.json` reproduce the
25 JSON cases from aiophyn commit
`42d35d61e338da4b34b5074490782a80419a31cd` (diagnostic implementation:
`f032a3a457cf5dfa4347bf991a244e80eff09273`). The original shared file SHA-256 is
`addd47554225e943f27a8bebe07b2b0b42fb27933ea44f9794df3c8938300f8c`;
the local copy uses different whitespace but identical JSON values.
Twenty-four raw-event cases apply directly. The catalog-only case is retained
as source evidence and exercised separately **without** its optional catalog:
the consumer does not fetch one. These are policy-conformance cases, not proof
of server revision semantics or observed live user corrections.

The helper tests cover precedence, all-candidate validation, ties and private-safe
review warnings. Recorder tests additionally cover unchanged saved labels on
restore, same-ID refetch moving contributions between categories without
changing total consumption or unrelated older rows, and preview without writes.
Pure-source probes cannot substitute for running these Recorder tests with the
real Home Assistant harness. Saved accepted labels are not fed back through the
raw-event resolver, including when recovering a pending journal.

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

## HACS development artifact and stable release

The editable loop is not a HACS install. **HACS installs the integration; HA
installs the Python dependency declared by its manifest.** Installing the fork
does not automatically select the aiophyn fork. The README's upstream HACS
distribution does not install this fork's fixture additions. Both integrations
use domain `phyn`; do not install upstream and this fork side by side.

The opt-in integration prerelease **v2026.9.2-beta.5** selects the public
**aiophyn 2026.9.2.dev1** wheel from the fork's **v2026.9.2.dev1** release.
The exact URL and SHA-256 are recorded in `custom_components/phyn/manifest.json`.
The library release includes its wheel, source distribution and checksums.
Tags point to feature-branch commits; neither repository needs a main merge.
These assets are distinct from upstream's public PyPI 2026.9.1.

Before publishing the integration release, require the library's artifact gates,
full paired tests, `pip check` and exact artifact/editable provenance, plus
HACS/hassfest validation. The `home-assistant-installer` job uses an official
HA 2026.9.3 container and `scripts/verify_development_install.py` to invoke HA's
real requirements installer against the manifest (including HA constraints).
It first rejects a deliberately incorrect hash through that installer, then
checks the correct artifact's installed version, import path and direct URL,
compares installed files with the independently hash-verified wheel, and imports
the integration. HA's uv may omit the hash from `direct_url.json`, so metadata
alone is not treated as checksum evidence. No account is configured, no integration is started, and no
Phyn device calls are made. It is not a live HACS UI or account-onboarding test.

Do not use moving refs, replaceable unhashed assets, private Actions artifacts,
editable paths or developer dependency-skip flags for rollout. Never rebuild
different bytes under a published development version/tag. For each library
change, issue a new library development build and an integration prerelease
pinning it; integration-only changes can retain the already-tested library.

## HACS development installation checklist

This checklist is for the isolated HA Container **2026.9.3** development
instance. A hostname is not permission for automation to access or change it.
The operator performs installation and restarts; production remains unchanged.

1. Record the current HA image tag and HACS/integration version. Back up the
   Docker deployment definition separately.
2. Create a named backup in **Settings > System > Backups**, including HA
   configuration and Recorder history/database. With the default SQLite
   backend, do not exclude the database. Download the completed backup outside
   the Docker host and retain its encryption emergency kit securely. Backups
   contain credentials; do not commit or paste their contents.
3. For an exact filesystem checkpoint, gracefully stop the dev container and
   archive/snapshot its whole configuration mount, including `.storage`,
   `custom_components`, configuration files and SQLite files, before restarting.
   Do not copy only a running SQLite main database while WAL data may exist.
4. Confirm the dev instance has independent config/database and no automations
   that operate real devices. The Phyn account/devices remain real even though
   HA is isolated. Do not invoke valves, leak tests, firmware updates, away or
   autoshutoff changes, or feedback writes during this read-oriented pilot.
5. In HACS, add `https://github.com/rsocko/homeassistant-phyn` as an
   **Integration** custom repository if it is not already present. There must be
   only one source managing `custom_components/phyn`. Preserve the saved Phyn
   config entry when replacing an existing installation.
6. Enable this repository's prerelease consideration if your HACS version
   requires it. In current HACS, find Phyn's **Pre-release** switch under
   **Settings > Devices & services > Entities**, filtered to HACS, including
   disabled entities. Enable the entity if needed, then turn its switch on
   ("Pre-releases preferred"). Return to HACS, select **Update information**,
   then **Download/Redownload > Need a different version? > v2026.9.2-beta.5**.
   A UI may omit the leading `v`.
   An installed/latest SHA such as `ff0004b` is the old default branch, not this
   release. If the selected version is absent, stop rather than installing the
   default branch or editing the installed manifest.
7. Restart HA normally. HACS should report `2026.9.2-beta.5`; HA must not report
   dependency/setup failures. HA installs the exact manifest wheel without a
   separate `pip install`, editable checkout or `--skip-pip` flag.
8. Configure Phyn in **Settings > Devices & services** only if it is not already
   configured. Normal polling can immediately import usage into the **dev**
   Recorder; a service dry-run does not disable these background imports.
9. Verify devices/entities and logs. The integration manifest version is
   `2026.9.2-beta.5`; installed library distribution and `aiophyn.__version__`
   must both be `2026.9.2.dev1`, with the manifest URL as installation provenance.
   The manifest checksum is exercised by CI's installer and installed-file checks;
   HA's uv may leave the metadata hash empty. A read-only inspection from the same container Python environment
   can use `importlib.metadata.distribution("aiophyn")`,
   `distribution.read_text("direct_url.json")` and `aiophyn.__file__`.
10. In **Developer Tools > Actions**, choose `phyn.import_fixture_statistics`
    with an entity from **one intended device**, explicit start/end datetimes
    for a completed day, and `dry_run: true`. Review device, dates, fetched
    events, projected rows/corrections, and zero cleared statistic IDs.
11. If approved, repeat that fixed range with `dry_run: false` to write dev
    statistics. Compare selected totals/categories with the Phyn app. Identical
    returned observations must not double-count on replay; cloud observations
    can change and a requested window does not prove completeness.
12. Restart or reload the integration deliberately and verify history remains,
    with no unexpected duplicate usage or fixture-state pause notice. Keep
    app-based attribution edits separate and deliberate: they modify the real
    account. HA review/editing remains future work (#1/#2 in this repository).

### Beta 5 inventory and Energy coverage checks

Upgrading a working beta 2, beta 3, or beta 4 installation requires no statistics reset, storage
file changes, or removal/recreation of the Phyn config entry. Historical series
IDs and the ledger schema are unchanged. The same pinned aiophyn development
wheel remains in use; this is not a new SDK release.

After updating through HACS and restarting HA, open **Settings > Devices &
services > Entities**, filter to **Phyn** (not HACS), and include disabled
entities. Enable a desired **Configured [category] count** sensor, then open
its existing Phyn Plus monitor's device page. Compare its value with that home's
configured count in the Phyn app. All API categories, including zero counts,
are offered disabled by default; they are not individual physical fixtures.

Counts refresh approximately hourly. A count entity's History shows recorded
inventory changes only. Keep using external `phyn:` statistics in Energy or a
Statistics Graph card for consumption; enabling counts creates no second usage
series. Zero/missing inventory never deletes or suppresses usage history.
An inventory failure should show unavailable counts and a logged refresh error,
not zero counts or a fixture-statistics reset. Do not deliberately mutate the
real Phyn inventory merely to check this installation.

After the next successful fixture import, check **Developer Tools > Statistics**
for **Phyn Toilet Water** with one selected usage monitor, or home-aware names
such as **Phyn Cape - Toilet Water** with multiple selected PP1/PP2 monitors.
Unselected monitors and Smart Water Sensors do not count toward this naming
rule. Identical home names are disambiguated with monitor identifiers.
Existing statistic IDs,
usage rows, and Energy selections remain unchanged; custom Energy names can
override the new display metadata. Positive-count inventory categories become
selectable external statistics even before usage arrives. These new entries have
no consumption rows until real events are imported, not a fabricated zero.
Inventory refresh is approximately hourly; registration occurs on the next
non-dry-run fixture import, normally within about 15 successful update cycles.

Under **Phyn > Configure**, optionally enable **Warn about imported water usage
missing from Energy**. The default is off. This checks all selected Phyn homes
every five minutes and creates one Repairs warning per home for positive imported
usage absent from **Energy > Individual water devices**. Add desired statistics
to Energy, or select intentional omissions in Phyn's coverage exclusion option.
On a later check the warning should clear when no omissions remain. Disabling the
option clears it immediately. No dashboard preferences are changed automatically,
and empty inventory-only statistics do not trigger warnings.

### Missing entity values during the pilot

The custom-integration "has not been tested by Home Assistant" warning is
expected; it does not diagnose an entity failure. Check the Phyn config entry's
setup status and a regular sensor's exact state in **Developer Tools > States**
(`unknown`, `unavailable`, or no entity) before attempting manual imports.
An alert event entity can have no value until a new alert occurs.

The first prerelease (`2026.9.2-beta.1`) has a confirmed startup Logbook error:
`custom_components.phyn.logbook` has no `async_describe_events`. Its helper
filename collides with HA's platform discovery. Version `2026.9.2-beta.2` renames the
helper to `logbook_helpers.py`; ordinary Logbook entries still use HA's built-in
event. This traceback alone does not establish why sensors lack values.
Install the new prerelease through HACS; do not edit the installed files,
replace the existing immutable release, or clear configuration/history.

### Updating and rollback

For a new candidate, take another named backup, select that exact integration
prerelease in HACS, restart, and repeat the bounded checks. Do not automatically
promote branch pushes into a running HA instance. Install the same immutable
candidate in production only after a successful pilot and separate approval.

Rollback must preserve the matched configuration/Phyn evidence **and** Recorder
database from the same checkpoint, plus the prior HA image and integration
source. Do not assume a code-only downgrade can read newer saved state, clear
history to resolve a pause, or combine an old ledger with newer statistics.
Restore rehearsals must use an isolated configuration; do not connect two test
copies to the same Recorder database.

This development release is not security certification or production-readiness
approval. Inherited dependency advisories were not all remediated.

### Eventual stable release

Stable order: publish the approved compatible aiophyn release first, update the
integration's manifest to its approved exact PyPI pin and align the test
baseline, then verify clean installation/full tests before releasing the
integration. A green candidate with version 2026.9.1 does not clear the existing
public 2026.9.1 gate. Release, deployment and live-device verification require
separate approval.
