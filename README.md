# homeassistant-phyn

Home Assistant custom component for interfacing with [Phyn](https://www.phyn.com) Smart Water Assistant and Kohler H2Wise+ by Phyn.

The integration's [IoT class is Cloud Polling](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/#classifiers), meaning the Home Assistant integration with this device happens via the Phyn cloud service. As such it requires an active internet connection to see updates and make changes, and Home Assistant polls the cloud service periodically for new state.

This integration currently provides the following capabilities:

- Daily water usage (compatible with Energy dashboard)
- Per-fixture water usage statistics, historical imports, and correction reloads (this fork)
- Average water temperature, pressure, and flow (realtime not available)
- Shutoff valve control
- Away mode control
- Autoshutoff control
- Scheduled Leak Test activation control

# Installation via HACS

This fork's opt-in development release is **2026.9.2-beta.2**, requiring
**Home Assistant 2026.9.3 or newer**. The tested baseline is 2026.9.3, not a
guarantee for every newer release. It is intended for backed-up development
instances first, not an automatic production upgrade.

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
   select **v2026.9.2-beta.2** (some UIs omit the `v`).
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
The 1-365 day/date request window is not a completeness guarantee. Labels describe
fixture categories, not proven individual household fixtures. Unsupported
developer state pauses fixture statistics only, with a persistent notice; normal
sensors and controls continue. See the runbook for readback/recovery limits;
there is no automatic migration or history clear.

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

Review and correct event attribution in the **Phyn app** for now. Home Assistant
offers aggregate statistics and import/reload previews, not an event attribution
editor. Future work is tracked separately:
[read-only review (#1)](https://github.com/rsocko/homeassistant-phyn/issues/1) and
[user-confirmed editing (#2)](https://github.com/rsocko/homeassistant-phyn/issues/2).

For fetched observations, an explicit, valid `latest_user_feedback.fixture_id`
takes precedence over model predictions. This is a **category ID**, not a unique
household fixture. Its label comes from matching suggestions already in the
response, or `Fixture type N` if no unambiguous matching name is available.
No additional catalog request is made. `sub_fixture_id` remains separate private
metadata in the observation; it is not used as a statistics bucket. Freeform
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
