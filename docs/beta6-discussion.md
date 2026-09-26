# Phyn category water usage: beta 6 available for testing

I have a new opt-in beta of my Home Assistant Phyn fork ready for testing:
**[v2026.9.2-beta.6](https://github.com/rsocko/homeassistant-phyn/releases/tag/v2026.9.2-beta.6)**.

The focus is Phyn Plus (PP1/PP2) **category-level water usage history** in Home
Assistant, including Energy dashboard statistics and corrections when Phyn
reclassifies an event.

**Included in this beta:**

- Historical category usage imports with correction-safe replay and recovery.
- Per-monitor **Backfill days**, **Backfill category history**, and persistent
  progress/status controls.
- Automatic sequential chunking for larger requests, with overlap handling so
  repeated event IDs do not count as additional consumption.
- Optional read-only configured inventory counts and advance registration of
  positive-count categories without inventing water usage.
- Clearer home/device labels and optional Repairs warnings for imported category
  usage missing from Energy's Individual water devices.

The history changes are covered by real HA Recorder tests for duplicates,
boundaries, corrections, interrupted writes, restart/retry, and dry runs. A
separate bounded live comparison found identical events and payloads between
a 31-day request and its chunked equivalent. That is useful evidence, **not a
guarantee that Phyn always returns complete history**.

**Testing requirements:** Home Assistant **2026.9.3 or newer** (2026.9.3 is the
tested baseline), HACS, and a backed-up development instance. HACS installs the
integration and HA installs its checksum-pinned SDK dependency automatically.
No manual Python package installation is needed.

**[Installation, feature explanations, and tester checklist](https://github.com/rsocko/homeassistant-phyn/blob/v2026.9.2-beta.6/docs/beta6-tester-guide.md)**

For an existing fork installation: refresh HACS information, select
**v2026.9.2-beta.6**, and restart HA. Preserve the existing Phyn configuration and
history; do not reset statistics or delete/re-add a working entry. This fork
shares the `phyn` domain with upstream, so they must not be installed side by side.

I would especially appreciate feedback on fresh installs/upgrades, fixed-range
replay, backfill progress, multi-home presentation, inventory counts, and Energy
configuration. Please report versions, device models, reproduction steps, and
redacted errors. Do not post credentials, raw household histories, or unreviewed
debug logs.

**Not included yet:** an event-review/editor panel, category-feedback writeback,
editable inventory, or automatic Energy configuration. Counts describe categories
configured in Phyn, not identified physical fixtures. The 365-day selector limit
is our chosen local guard, not a discovered API history maximum.

This remains a development prerelease, not a production-readiness guarantee.
Beta 6 supersedes betas 1-5; the older releases remain available for reference.
