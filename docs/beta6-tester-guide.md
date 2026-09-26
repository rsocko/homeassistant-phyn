# Phyn Water Usage beta 6: installation and testing

**Release:** [v2026.9.2-beta.6](https://github.com/rsocko/homeassistant-phyn/releases/tag/v2026.9.2-beta.6)

This is an opt-in development prerelease for backed-up Home Assistant instances,
not a production-readiness guarantee. It includes the completed water-usage
features from earlier betas plus native backfill controls and automatic chunking.
Betas 1-5 are superseded; their releases and tags remain for historical reference.

## Requirements and scope

- Home Assistant **2026.9.3 or newer**. The tested baseline is **2026.9.3**;
  compatibility with every newer release is not guaranteed.
- HACS, internet connectivity, and a Phyn account with a selected Phyn Plus
  **PP1 or PP2** monitor for category usage, inventory counts, and backfill controls.
- Existing PC1/PW1 functionality remains, but category-usage additions do not
  provide equivalent functionality for those device types.
- A backup including HA configuration, integration storage, and Recorder history.
  For this pilot, prefer a development instance without automations that operate
  real water controls.

HA installs the exact public **aiophyn 2026.9.2.dev1** wheel declared in the
integration manifest, with a SHA-256 pin. No manual `pip` installation, SDK update,
or moving branch selection is needed. This release does not change account
management or add inventory/attribution writeback.

## What is included

| Feature | What to expect |
|---|---|
| Category usage history | External `phyn:` water statistics for Energy and Statistics Graph cards, separate from ordinary entity History |
| Corrections and replay | Re-fetched observations reconcile by device-scoped event ID; unchanged repeats do not double-count consumption |
| Native backfill controls | **Backfill days**, **Backfill category history**, and **History backfill status** under each selected Plus monitor |
| Chunked imports | Sequential seven-day nominal windows, one-millisecond internal overlap, one-second pacing, and per-chunk Recorder verification |
| Progress and recovery | Completed/total chunks, remaining range, counters, and last successful completion; failures retain verified earlier chunks |
| Configured inventory counts | Optional read-only category-count sensors, disabled by default; values reflect the Phyn app's configured inventory |
| Advance category registration | Positive-count categories get selectable statistics metadata during an import, without fabricated usage rows |
| Home-aware presentation | Clear setup labels; statistic names include the home when multiple usage monitors are selected |
| Energy coverage warnings | Optional Repairs for positive imported category usage not selected under Energy's Individual water devices |

The release also retains the existing monitor sensors and controls. Installing
the integration does not itself press valve, leak-test, or other control buttons.
Normal polling does begin fetching data and can write history into HA.

## Install or upgrade using HACS

1. Back up configuration **and Recorder history together**, download the backup,
   and retain its recovery credentials securely. If you need a filesystem snapshot,
   follow the [technical backup instructions](testing-runbook.md#hacs-development-installation-checklist);
   do not copy only a running SQLite database file.
2. In HACS, open **Custom repositories** and add
   `https://github.com/rsocko/homeassistant-phyn` with category **Integration**.
   If this fork is already configured in HACS, use that existing repository.
3. Enable prereleases for the repository. Depending on HACS version, find its
   **Pre-release** switch in **Settings > Devices & services > Entities**,
   filtered to HACS with disabled entities visible. Enable it if needed and turn
   it on. Return to the repository and choose **Update information**.
4. Choose **Download** or **Redownload**, then **Need a different version?**
   and select **v2026.9.2-beta.6**. Some versions omit the leading `v`.
   Do not choose the moving feature branch or accept a commit hash as the release.
5. Restart Home Assistant. Verify that Phyn reports **2026.9.2-beta.6** and
   has no dependency or setup errors.
6. If Phyn is already configured, **keep the existing config entry**.
   For a fresh installation, go to **Settings > Devices & services > Add
   integration**, select Phyn, sign in, and choose the intended monitors.
   The selection labels include home, device name/model, and identifier.

Upstream and this fork share the `phyn` domain and installation directory:
**do not install them side by side**. Replace the source code managed by HACS,
not the saved configuration. A working beta 2-5 upgrade needs no statistics reset,
storage-file edits, or delete/re-add. Beta 1 is superseded and has a known Logbook
defect; do not use it for new testing.

HACS wording varies. If beta 6 is not visible, refresh repository information
and check prerelease settings rather than editing the installed manifest.

## First-run checklist

### 1. Check normal operation and device selection

Confirm that only the intended monitors are selected, expected sensor values
appear, and logs have no new setup/import errors. An alert event entity can
remain unknown until a new alert occurs. Reconfiguring selection should preserve
intentionally deselected homes.

Do not operate valves, leak tests, firmware updates, away mode, or autoshutoff
solely for this read-oriented pilot. A development HA instance still connects
to real devices and the real Phyn account.

### 2. Inspect a bounded dry run

In **Developer Tools > Actions**, select **Phyn: Import fixture statistics**
(`phyn.import_fixture_statistics`). Choose any entity belonging to **one intended
monitor**, supply both start and end for a completed day, and enable **Dry run**.
Leaving the device/entity unspecified can target all compatible selected monitors.

Review the returned dates, device, projected rows, correction count, fetched
observations, unique events, duplicate observations, and chunk count. Cleared
statistic IDs should be zero. Use explicit timezone offsets when supplying dates
outside the UI; naive action datetimes are interpreted as UTC.

A dry run does not change statistics, inventory metadata, accepted evidence,
or backfill progress. It does **not** disable normal background imports, which
may continue independently.

### 3. Try the device backfill controls

Open the intended Plus monitor's device page:

- Under Configuration, set **Backfill days** to **1** initially.
  Changing this number must not start a job.
- Press **Backfill category history**. Its range ends at the press time;
  it is an elapsed-day window, not necessarily a completed local calendar day.
- Inspect **History backfill status** under Diagnostics. It should move through
  running to completed or a visible failure/interruption. A quick job may finish
  before you see running.
- Open the status details to see its range, chunk progress, counters, and last
  successful completion. The button timestamp means **pressed**, not **successful**.

After a small request works, **14 days** exercises multiple chunks. Normal
monitor updates should continue during the job. Do not repeatedly press while
it is running; overlapping button requests are rejected.

The status describes button-requested backfills, not recurring imports or actions
started through Developer Tools. Updating days during a job affects the next press.

### 4. Check repeatability without double-counting

Use **Developer Tools > Actions** to import the same explicit completed-day range
twice, with Dry run off, targeting the same monitor. For unchanged cloud
observations, the second import should add no consumption. Inspect external
statistics or a Statistics Graph card, not the count sensor's History.

The button uses a new ending time each press, so it is not an exact fixed-range
replay test. Phyn can revise events between calls; changed values alone do not
prove duplication. Report the selected range and counters if the results differ.

### 5. Check inventory and Energy

Count entities start disabled. In **Settings > Devices & services > Entities**,
filter to Phyn, include disabled entities, and enable a desired
**Configured [category] count**. Compare it with the matching home's inventory
in the Phyn app; it is neither a detected physical device nor a volume.

Inventory refreshes approximately hourly. Positive-count category statistics
are registered on the next non-dry-run history import after inventory is known.
Metadata-only categories can be selected before usage arrives; they have no
consumption rows yet. A category with count zero can still have valid usage.

In **Developer Tools > Statistics**, find the external `phyn:` category series.
Names omit the home with one selected usage monitor and include it with multiple
selected monitors; identical home names include monitor identifiers. Energy's
custom names can override these labels.

Add desired series under **Energy > Individual water devices**, or use a native
Statistics Graph card. Do not add category totals a second time as an additional
whole-house water source. With multiple homes, Energy is instance-wide:
deliberately choose which monitors/categories belong in that dashboard.

Optionally enable **Warn about imported water usage missing from Energy** in
Phyn's options. Checks run about every five minutes across all selected homes.
Adding the desired statistics, excluding intentional omissions in Phyn's options,
or disabling the feature clears the corresponding warning on the next applicable
check. The integration does not edit Energy preferences.

### 6. Reload and verify persistence

After a job completes, reload the integration or restart HA. Verify that selected
days, the last outcome, and historical statistics remain. Existing statistic IDs
and Energy selections should be unchanged.

If a job was interrupted, expect an interrupted/unconfirmed outcome, not an
automatic restart. Do not interrupt a production instance just to test this.

## Understanding outcomes and limits

- **Completed** means returned observations were processed and required Recorder
  rows verified, not that the API returned all cloud history.
- **Events fetched** includes repeated observations. **Events unique** counts
  distinct IDs for the monitor; their difference is **Duplicate events**.
  Repeated observations are not added as additional water usage.
- **Imported rows** counts planned/verified rows across chunks, not events or
  gallons. An hour can appear more than once when a later chunk updates it.
  A dry run previews the combined final observations once, so its row count
  need not match the live sum across chunks.
- The **365-day selector maximum is an arbitrary local guard**, not a discovered
  API limit. Explicit start/end actions are not capped at 365 days. A live
  366-day request succeeded; a 730-day single request returned HTTP 504, which
  did not establish a maximum. Large ranges still cost requests, time, memory,
  and retained storage despite chunking.
- In a bounded live 31-day comparison, whole-range and chunked responses matched
  exactly. Synthetic actual-Recorder tests cover boundaries, duplicates,
  corrections, failures, and retries. These do not guarantee cloud completeness.
- History uses event close-time UTC hours, not duration-based allocation across
  hours. Phyn app totals can differ because of filtering, time boundaries,
  delayed processing, or attribution changes.
- Statistics remain category-label based. A cloud category rename can create a
  separate series; there is no automatic label-to-category-ID migration.
- Corrections apply when events are fetched again; recurring overlap does not
  continuously refetch all stored history. Omitted events are not treated as
  deletions. Event splits/merges and server revision ordering are not established.
- Accepted evidence is retained without automatic expiry. This is intentional
  for reconciliation but means storage can grow.

## If an import fails

Stop and inspect the status/error and logs. Earlier verified chunks remain;
the current chunk may have a pending recovery journal. Retry the original fixed
range or the reported remaining range through the date-based action. No
automatic retry or resume is performed. A repeated import must reconcile
observations rather than clear history.

If HA reports that statistics differ from saved evidence or that fixture
statistics are paused, preserve the evidence and report it. **Do not delete
`.storage` files, reset statistics, or directly edit managed Recorder values**
to bypass the check.

Rollback requires matching configuration/Phyn evidence **and Recorder database**
from the same backup, plus the prior integration and HA versions. Keeping old
release tags does not make a code-only downgrade safe. See the
[rollback instructions](testing-runbook.md#updating-and-rollback).

## Report results safely

Open an [issue](https://github.com/rsocko/homeassistant-phyn/issues) for a
reproducible problem. Include:

- Integration version **2026.9.2-beta.6**, HA version, installation type, and HACS
  version; fresh install versus upgrade and the previous integration version.
- Monitor model(s), number of selected monitors, and whether multiple homes
  are involved. Use aliases, not home addresses or device identifiers.
- The action/control used, expected versus actual behavior, selected duration
  or date range/timezone, chunk progress, and relevant counters. Omit exact
  activity times/counts if they are sensitive; start with a redacted summary.
- Whether fixed-range replay or reload changes the result, and a **reviewed,
  redacted** traceback/log excerpt if available.

Never post passwords, tokens, full backups, `.storage` files, complete raw API
responses, unreviewed debug logs, or household activity histories. Debug logs
can contain home/device metadata. Crop screenshots to remove identifying data.
No live Phyn feedback or device-control writes are needed to report a history
problem.

## Not included / later phases

A read-only event-review panel, user-confirmed category feedback, editable
inventory counts, inventory-mismatch warnings, and richer diagnostics are future
work. There is no bundled history editor in beta 6, no automatic Energy setup,
and no supported editing of event volume/time or event deletion/splitting/merging.

This guide is a test procedure, not a claim that a live installation/upgrade
rehearsal has already been completed for your environment. Complete the bounded
checks on Dev before considering a production rollout.
