# homeassistant-phyn

Home Assistant custom component for interfacing with [Phyn](https://www.phyn.com) Smart Water Assistant and Kohler H2Wise+ by Phyn.

The integration's [IoT class is Cloud Polling](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/#classifiers), meaning the Home Assistant integration with this device happens via the Phyn cloud service. As such it requires an active internet connection to see updates and make changes, and Home Assistant polls the cloud service periodically for new state.

This integration currently provides the following capabilities:

- Daily water usage (compatible with Energy dashboard)
- Per-fixture water usage statistics, historical imports, and correction reloads (this fork)
- Optional read-only configured fixture-category counts for Phyn Plus (this fork)
- Per-monitor history backfill controls with chunked imports and progress (this fork)
- Optional warnings for imported category usage missing from Energy (this fork)
- Average water temperature, pressure, and flow (realtime not available)
- Shutoff valve control
- Away mode control
- Autoshutoff control
- Scheduled Leak Test activation control

# Installation via HACS

This fork's opt-in development release is **2026.9.2-beta.7**, requiring
**Home Assistant 2026.9.3 or newer**. The tested baseline is 2026.9.3, not a
guarantee for every newer release. It is intended for backed-up development
instances first, not an automatic production upgrade.

**Start here:** [Beta 7 installation, features, and tester checklist](docs/beta7-tester-guide.md).
For a short announcement, see the [copy-ready GitHub discussion draft](docs/beta6-discussion.md).
Beta 7 supersedes betas 1-6; their releases and tags remain available for historical
reference. Use beta 7 for new testing.

HACS downloads the integration; HA installs its exact, checksum-pinned aiophyn
development wheel. **Do not install aiophyn manually or select the moving
`feature/fixture-usage` branch.** See the
[development installation and rollback checklist](docs/testing-runbook.md#hacs-development-installation-checklist).

1. Back up the development instance, including its Recorder database.
2. In HACS, open **Custom repositories**, add
   `https://github.com/rsocko/homeassistant-phyn`, and select **Integration**.
3. Enable prerelease consideration for this repository if required by your HACS
   version. In current HACS, find Phyn's **Pre-release** switch in **Settings >
   Devices & services > Entities**, filtered to HACS; include disabled entities.
   Enable the entity if disabled, then turn the switch on. Return to HACS and
   select **Update information**.
4. Choose **Download** or **Redownload**, open **Need a different version?**, and
   select **v2026.9.2-beta.7** (some UIs omit the `v`).
5. Restart Home Assistant and verify the installed version before configuring
   Phyn or running the bounded checks in the runbook.

An installed/latest value of `ff0004b` means HACS is following this fork's old
default branch, **not** this development release. Do not proceed until the
selected prerelease is visible. Upstream and this fork both use domain `phyn`;
replace the existing code source, not the saved integration configuration, and
do not install both side by side. For the upstream distribution rather than
this fork, see [jordanruthe/homeassistant-phyn](https://github.com/jordanruthe/homeassistant-phyn).

# Configuration

Configuration is done via the UI. Add the "Phyn" integration via the Integration settings and provide existing Phyn username and password.

During setup and reconfiguration, each device option includes its Phyn home,
device name (or readable model), and identifier, for example
**Cape - Main line (device ID)**. Identically named homes have their home IDs
added to the headings so neither home's devices are hidden. Reconfiguration
preserves homes with no selected devices rather than reselecting them.

* In the Home Assistant UI, go to Settings > Devices & services, go to the Devices tab, and click "+ Add Device" on the bottom right.

* Search for and select "Phyn".

* A prompt will appear for you to enter your Phyn Account username and password. (This could sometimes take 2-3 minutes, or longer).

# Translations

[![Translation status](https://hosted.weblate.org/widget/homeassistant-phyn/homeassistant-phyn/svg-badge.svg)](https://hosted.weblate.org/engage/homeassistant-phyn/)

Translations for this integration are managed with [Weblate](https://weblate.org/), a
libre web-based continuous localization platform. Weblate generously provides free
hosting for this project under its [Libre plan](https://weblate.org/hosting/).

Want to help translate Phyn into your language? No GitHub account or coding required —
just head to the [Phyn project on Hosted Weblate](https://hosted.weblate.org/engage/homeassistant-phyn/)
and start translating. Contributions are batched into pull requests automatically.

[![Translation status](https://hosted.weblate.org/widget/homeassistant-phyn/homeassistant-phyn/multi-auto.svg)](https://hosted.weblate.org/engage/homeassistant-phyn/)

For contributors: `strings.json` is the source of truth for the integration's English
strings (config flow, entity names, service names) and may reference Home Assistant's
shared strings via `[%key:...%]`. `translations/en.json` is the literal-English template
Weblate translates from — it's generated, not hand-edited. After changing `strings.json`,
regenerate it with:

```
python3 scripts/sync_translations.py
```

CI runs `scripts/sync_translations.py --check` to make sure the two never drift. The
other `translations/*.json` files are owned by Weblate; hand edits to them get
overwritten on the next Weblate sync.

# Tracking usage since a date (e.g. cistern fills)

If you draw water from a cistern (or any fixed-capacity tank) and need to know how much has been used since the last fill — so you can trigger a "refill needed" notification — you can do this entirely with built-in Home Assistant helpers. No extra integration code is required.

## Which sensor to use

**Phyn Plus (PP1/PP2) devices** expose a **"Total Water Usage"** sensor
(`sensor.<device>_total_water_usage`) that is a cumulative, ever-increasing meter sourced
from the device's real-time MQTT feed. This is the best source for this use-case.

**Phyn Classic (PC1) devices** do not have a cumulative meter; use the **"Daily water usage"**
sensor (`sensor.<device>_daily_consumption`) instead. The `utility_meter` will accumulate
daily totals across days without resetting automatically.

## Step 1 — Create a utility_meter helper

Add the following to your `configuration.yaml` (adjust the `source` entity ID to match
your actual device):

```yaml
utility_meter:
  cistern_usage_since_fill:
    source: sensor.phyn_total_water_usage   # adjust to your entity id
    # No "cycle:" key → meter accumulates indefinitely until manually reset
```

Restart Home Assistant. A new sensor `sensor.cistern_usage_since_fill` will appear,
showing gallons used since the meter was last reset.

> **Tip:** Find your exact entity ID in **Settings → Devices & services → Phyn → entities**.

## Step 2 — Record the fill date (optional)

Create an **Input Datetime** helper to log when each fill happened
(**Settings → Devices & services → Helpers → + Create helper → Date and/or time**),
e.g. named `Cistern fill date` → entity `input_datetime.cistern_fill_date`.

## Step 3 — Reset the meter on each fill

When you refill the cistern, reset the `utility_meter` via **Developer Tools → Actions**:

| Field | Value |
|-------|-------|
| Action | `utility_meter.reset` |
| Targets (entity) | `sensor.cistern_usage_since_fill` |

Or, in an automation triggered by a dashboard button, a physical button helper, etc.:

```yaml
action:
  - service: utility_meter.reset
    target:
      entity_id: sensor.cistern_usage_since_fill
  - service: input_datetime.set_datetime
    target:
      entity_id: input_datetime.cistern_fill_date
    data:
      datetime: "{{ now().isoformat() }}"
```

## Step 4 — Automate the refill alert

```yaml
automation:
  - alias: "Cistern refill needed"
    trigger:
      - platform: numeric_state
        entity_id: sensor.cistern_usage_since_fill
        above: 900        # gallons used since fill; adjust to your cistern capacity
    action:
      - service: notify.notify
        data:
          title: "Cistern refill needed"
          message: >
            {{ states('sensor.cistern_usage_since_fill') }} gal used since the
            last fill on {{ states('input_datetime.cistern_fill_date') }}.
```

For longer-term trends, the **"Daily water usage"** sensor is already compatible with the
Home Assistant **Energy / Water** dashboard.

**Further reading:**
- [utility_meter integration](https://www.home-assistant.io/integrations/utility_meter/)
- [input_datetime integration](https://www.home-assistant.io/integrations/input_datetime/)

# Viewing PW1 environmental history (temperature / humidity / battery)

The PW1 water sensor imports authoritative hourly statistics (mean, min, max) for
its Air Temperature, Humidity, and Battery sensors directly from the Phyn cloud API.
These statistics are stored under external `phyn:` statistic IDs, separate from the
sensors' own recorder-compiled statistics, so the two never conflict.

The standard more-info popup graph for each sensor shows the recorder's short-term
history. To view the richer Phyn-imported hourly history, use a **Statistics Graph
card** and point it at the `phyn:` statistic IDs. You can find the exact IDs for your
device in **Developer Tools → Statistics**; they follow the pattern
`phyn:<device_id>_<metric>` (e.g. `phyn:deviceid_humidity`).

```yaml
type: statistics-graph
title: PW1 Environmental History (Phyn API)
entities:
  - phyn:<your_device_id>_air_temperature
  - phyn:<your_device_id>_humidity
  - phyn:<your_device_id>_battery
stat_types:
  - mean
  - min
  - max
period: hour
```

Replace `<your_device_id>` with your device's ID slug from **Developer Tools →
Statistics**.

# Known Issues

* Phyn home name (in the Phyn App > Settings > Home > Address > Home Name) cannot be set to "Home" or integration configuration and setup will fail.

* If get an (API) error when trying to first initialize saying "User Not Found" then take note that Phyn username e-mail address is case sensitive.

## Developer note

The base entity classes have been consolidated into a single canonical location: `custom_components/phyn/entities/base.py`. The legacy `custom_components/phyn/entity.py` file has been completely removed to eliminate duplicate class definitions. If you maintain local forks or external code that imports from the old path, please update imports to use `..entities.base` (for internal package imports) or `custom_components.phyn.entities.base` as appropriate.

## Development and Testing

See the [testing runbook](docs/testing-runbook.md) for the Linux-first editable
library loop, provenance checks, isolated HA Core development and release gates.

### Running Tests Locally

Use Linux and Python 3.14. The pinned test harness
`pytest-homeassistant-custom-component==0.13.366` selects HA 2026.9.3.
**Published aiophyn 2026.9.1 currently conflicts with that HA dependency graph**
(`pycognito<2023` versus `pycognito==2024.5.1`); the released-dependency lane
must fail visibly until a compatible public release is approved.

For paired development, create a fresh environment and resolve the explicitly
selected editable aiophyn checkout **together with** the test requirements:

```bash
# In a fresh, activated Linux Python 3.14 environment:
python -m pip install -r requirements_test.txt -e "$AIOPHYN_CHECKOUT"
python -m pip check
# Verify import/direct_url provenance as shown in the runbook before testing.
python -m pytest tests/ -v --disable-socket --allow-unix-socket
```

Candidate and published distributions can report the same version; an import
path and exact source commit are required evidence. Do not install incompatible
published requirements first and override afterward, bypass dependencies, or
pip-install manually into an HA OS host/production environment.

Fixture imports retain accepted contributions for all imported history, without
expiry. Corrections use the last locally observed contribution per device-scoped
event ID, not a proven newest server revision. Omitted events are not deleted.
`force_reimport: true` and reload reconcile observations without clearing whole
series; use `dry_run: true` with an explicit timeframe to preview writes.
The 1-365 day selector uses an arbitrarily chosen local guard, not a discovered
SDK/API maximum, proven retention limit, or guarantee that a request will succeed.
Explicit date ranges are not capped at 365 days. Neither guarantees complete
returned history. Labels describe
fixture categories, not proven individual household fixtures. Unsupported
developer state pauses fixture statistics only, with a persistent notice; normal
sensors and controls continue. See the runbook for readback/recovery limits;
there is no automatic migration or history clear.

### Device history backfill controls (beta 6)

Each selected Phyn Plus (PP1/PP2) monitor offers native controls on its device
page, under Configuration:

- **Backfill days**: days to request, from 1 to 365, initially 7. The maximum
  is an arbitrarily chosen local guard, not a discovered API limit. This local
  setting survives reloads/restarts; changing it does not start an import.
- **Backfill category history**: requests that monitor's category events for
  the selected number of elapsed days ending at the press time, in UTC.
  The job runs in the background without blocking normal sensor refreshes.
- **History backfill status** (Diagnostic): idle, running, completed, failed,
  or interrupted. Attributes show the requested range, timestamps, fetched
  observations, unique event IDs, repeated observations, imported rows, detected
  corrections, completed/total chunks, remaining date range, and last successful
  completion.

The button's own timestamp means **pressed**, not **success**. Check the status
sensor instead. Completed means the returned observations were processed and
any required Recorder writes verified, **not** that Phyn returned every event.
An empty response may complete with zero events; it does not prove no usage.
The status tracks button-requested backfills only, not recurring imports or
Developer Tools actions. Changing days during a run affects the next press.

Duplicate presses are rejected while a device import is active. All imports
share the existing per-monitor lock. Different monitors have separate settings
and outcomes. Cloud-history controls do not require the physical monitor to be
online. Failure details are logged; unload cancels the button's job, and an
unconfirmed run is shown as interrupted after restart, never resumed
automatically. The last successful completion survives failures and restarts.
Interrupted/failed work may have written rows or a recovery journal; a later
import uses the existing reconciliation/recovery path rather than clearing data.

Large requests from these controls, the existing actions, and recurring imports
are automatically split into **sequential seven-day nominal windows**, with a
one-millisecond overlap at each internal boundary and a one-second pause between
chunks. Each chunk commits through the existing correction-safe importer and
Recorder readback before the next request. The per-monitor lock covers the whole
operation. Repeated observations never add a second contribution for an event ID;
a changed observation in a later response revises the earlier contribution.
Contradictory duplicates within a single response fail explicitly.

On failure, later chunks are not fetched. Earlier verified chunks remain, and
the error/status identifies the remaining range. Retry that range through the
date-based action, or safely repeat the original request. A chunk interrupted
after writes may have a pending journal; normal retry/recovery handles it.
There is no automatic retry or resume. Dry runs instead fetch and validate all
chunks, then preview the combined last-observed event map once, without writing
statistics, inventory metadata, saved evidence, or backfill progress. Dry runs
hold that combined event map in memory.
Unloading the integration also stops manual actions before their next chunk
fetch; an already in-flight chunk can finish through the normal commit path.

`events_fetched` counts received observations, including overlaps/duplicates;
`events_unique` counts distinct device-scoped event IDs and `duplicate_events`
is their difference. Neither counter is a water volume. Live `imported_rows`
counts planned/verified rows across chunks (an hour may appear more than once), while a
dry run reports the net projected rows for the combined final observations.

These controls use the same correction-safe importer as
`phyn.import_fixture_statistics`; they do not reset statistics, change existing
IDs, create sensor-generated water statistics, or write to Phyn. Advanced
start/end dates and dry runs remain available in **Developer Tools > Actions**.
These controls and automatic chunking are included starting with beta 6.

**The maximum API history depth is not established.** A bounded read-only probe
on September 26, 2026 accepted 1, 7, 31, 90, 365, and 366-day requests on one
monitor. The 366-day response included an additional day's events beyond the
365-day response: 365 is not an API ceiling for that monitor. A 730-day request
returned HTTP 504 after about 29 seconds; testing stopped at that server error.
This was not explicit date/length validation and does not establish a maximum.

The SDK method still makes one logical history request without pagination or a
proven completeness/result-cap guarantee; HA now calls it once per chunk.
In a separate bounded live comparison, a completed 31-day whole-range response,
the union of five chunk responses, and a repeated whole-range response had
identical event ID sets and decoded payloads. There were no missing, extra, or
changed events in that sample. No event landed within one millisecond of its
internal boundaries, so synthetic offline HA tests cover boundary duplicates,
long cross-boundary events, corrections, interrupted commits, and safe retries.

These results establish sampled whole/chunk equivalence, not global completeness,
universal retention, or a proven server boundary/filter contract. Chunking cannot
recover events the API never returns. The device control retains its arbitrarily
chosen 365-day local guard, not a demonstrated safe maximum for every monitor.
Any further retention investigation should use bounded older known-activity
windows and repeated/split-window comparisons. An empty old window alone is
not evidence of a retention limit.

### Optional configured fixture counts

Phyn Plus (PP1/PP2) monitors expose read-only **Configured [category] count**
sensors, such as "Configured Toilet count". These are the inventory counts
configured in the Phyn app, not detected fixtures, individual physical devices,
or consumption measurements. They belong to the existing Phyn monitor.

Count sensors are **disabled by default**. In **Settings > Devices & services >
Entities**, filter by the Phyn integration, show disabled entities, and enable
only the configured counts you want. Each category returned by Phyn is available,
including zero-count categories; a zero count never suppresses usage history.
IDs use the monitor and category ID, not the category's display name.

Inventory is fetched at sensor setup and approximately hourly, independently of
monitor and usage refreshes, including when all count entities are disabled so
new categories can be discovered. New categories also start disabled. Missing
categories or failed/invalid responses make affected counts unavailable, not
zero; a successful later refresh restores them. Existing entities are not
deleted when a count becomes zero or a category disappears. Changes to counts
must be made in the Phyn app; Home Assistant provides no inventory writeback.

These entities have no water device class, volume unit, or statistics-generating
state class, so they are not Energy water-consumption sources. Their ordinary
entity History shows recorded inventory-count changes only, not imported usage.
There are no additional category usage-summary entities or duplicate consumption
statistics. Existing imported `phyn:` statistics remain the category usage source
and retain their existing IDs, correction handling, and attribution limitations.

For a native dashboard, put selected count entities in an **Entities** card
titled "Configured inventory", beside a **Statistics Graph** card titled
"Historical category water usage". In the graph, select the corresponding
external `phyn:` statistic IDs from **Developer Tools > Statistics**, use
`stat_types: [change]`, `period: day`, and `chart_type: bar`. These graphs need no
count entities to exist or be enabled. Energy's **Individual water devices** can
also use the same external statistics. Do not substitute a count sensor or add
category usage again as a second whole-house water source. This is dashboard
presentation, not a link between entity History and external statistics.

### Fixture attribution and review

Starting with beta 4, external category statistics include the Phyn home
name, for example **Phyn Cape - Toilet Water**. This uses the home name from
Phyn, not a Home Assistant device nickname, and falls back to an identifier if
the name is unavailable. Reload the integration after renaming a home in Phyn.
Starting with beta 5, the home prefix is shown only when more than one
category-usage monitor (PP1/PP2) is selected in this HA integration. With one
selected monitor the name is **Phyn Toilet Water**. Unselected monitors and
Smart Water Sensors do not affect this rule. If selected monitors have identical
home names, their device identifiers are included to distinguish them. Changing
device selection refreshes display names without changing statistic IDs.
The next successful non-dry-run fixture import refreshes existing display names,
including categories absent from the latest event batch. Statistic IDs, category
labels in saved evidence, consumption rows, and Energy selections are preserved.
Dry runs never rename statistics. An explicit custom name in Energy can still
override the statistic's display name.

Categories with a positive configured inventory count are registered as external
statistics during the next non-dry-run fixture import after inventory refresh
(normally within about 15 successful monitor update cycles). Registration uses
metadata only: no event, consumption value, zero row, or baseline is invented.
They can be selected in Energy before usage arrives, but graphs have no data
until actual events are imported. Count entities do not need to be enabled.
Existing label-based statistic IDs are reused; registration does not rewrite
attribution or join differently named series. A later API category-name change
or ID-only event label may therefore produce a separate series, as with existing
event imports. Zero-count categories still get statistics when usage is observed.
Falling counts, missing categories, and inventory failures never delete history
or previously registered metadata. Registration is journaled with the existing
ledger so interrupted writes can recover without orphaning a series.

Under **Phyn > Configure**, optionally enable **Warn about imported water usage
missing from Energy**. This defaults off and checks **all selected Phyn homes**,
not just the home hosting HA. Every five minutes it compares categories with
positive accepted event contributions against **Energy > Individual water
devices**, and creates one aggregated **Settings > System > Repairs** warning per
home for omissions. Empty inventory-only statistics do not trigger warnings.
This checks all retained imported history, not only Energy's displayed date range.
Corrections that move all positive contributions away from a category remove it
from the check. Pending, uncommitted contributions do not trigger warnings.

Add the desired statistics to Energy, or use **Intentionally omit these usage
statistics from coverage warnings** in Phyn's options. Warnings clear on a later
check when covered, excluded, or no longer backed by positive contributions;
disabling the option clears them immediately. Energy custom names and upstream
relationships do not change the statistic IDs used for comparison. Selecting
only a parent whole-house meter does not include its categories automatically.
No Energy preferences are changed, and there is no automatic dashboard setup.
These warnings indicate dashboard omissions, not complete cloud coverage or
correct Phyn attribution. Missing history is not measured zero consumption.

Review and correct event attribution in the **Phyn app** for now. Home Assistant
offers aggregate statistics and import/reload previews, not an event attribution
editor. Future work is tracked separately:
[read-only review (#1)](https://github.com/rsocko/homeassistant-phyn/issues/1) and
[user-confirmed editing (#2)](https://github.com/rsocko/homeassistant-phyn/issues/2).

For fetched observations, an explicit, valid `latest_user_feedback.fixture_id`
takes precedence over model predictions. This is a **category ID**, not a unique
household fixture. Its label comes from matching suggestions already in the
response, or `Fixture type N` if no unambiguous matching name is available.
No additional catalog request is made for attribution. Unrecognized feedback
metadata is ignored, not retained in attribution diagnostics or the ledger. Freeform
`tell_us`, unsupported label fields, and an algorithm called `user-feedback`
are not substitutes for an explicit category selection.

Without that selection, all model candidates are validated and the greatest
finite confidence in `[0, 1]` wins (numeric strings are accepted). Exact ties use
the first **maximum**, with a review warning; reordering tied candidates can
therefore change provisional attribution. Missing predictions produce `Unknown`.
Malformed feedback or model candidates reject the batch rather than silently
skipping potentially dominant candidates. A valid human choice survives invalid
optional model metadata with a warning and an ID-based label; model confidence
is never assigned to the human choice. Review warnings contain reason codes,
not private feedback text.

Upgrading does **not** reinterpret saved event labels on startup. Subsequent
normal polling or requested imports can reattribute a refetched event under this
policy, including events previously labeled from freeform text or the first
prediction. Existing reconciliation adjusts both former and new category series
and their downstream sums; omitted events and unrelated history are retained.
No automatic all-history reimport is performed. The compact ledger retains
accepted contributions, not raw feedback, predictions, or household metadata.
This remains a last-observed **local policy**, not proof of server freshness,
revision ordering, complete history, or stable IDs across reprocessing.

This fork combines the fixture-usage features with upstream `main` at `10e4409`
(2026-09-13). Fixture imports require the epoch-millisecond `from_ts`/`to_ts`
library API even if a candidate also supports datetime bounds. The HACS
instructions above select this fork's explicit development prerelease.
HACS installs the integration; HA installs its manifest dependency. The fork's
development artifact and a future stable PyPI release have separate gates.

### Continuous Integration

Normal PR/main Validation keeps hassfest, HACS, translations and mypy checks,
plus the full offline suite against an explicit published aiophyn baseline.
The public dependency conflict above remains a failing gate, independent of
candidate results.

The existing Phase 2 runner supports published packages or a full immutable
40-hex commit in `rsocko/aiophyn`, with no moving candidate default. It defaults
to all tests; the targeted selector includes Recorder and lifecycle cases.
Manual dispatch requires the workflow on the default branch; until registered,
the push-triggered feature validation workflow is the validation route, not
evidence of a tested dispatch. It checks the manifest artifact, an exact editable
library revision, the actual HA dependency installer in an official disposable
container, and HACS/hassfest package validation.

Coverage includes fixture aggregation/corrections, registered services, installed
library API boundaries, real Recorder readback/recovery, and fixture lifecycle/
unload behavior, alongside basic integration-file checks. This is not full
config-flow, migration, reauth/reconfigure or live-device coverage, and does not
establish support below HA 2026.9.3 or Bronze quality certification.
The runbook records exact candidate-only evidence and remaining release gates.
