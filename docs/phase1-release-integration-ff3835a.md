# Phase 1 release integration on frozen production base ff3835a

## Immutable inputs

- Frozen production base: `ff3835a630deb1f03054806d91ae5712b76f16d1`
- Prior production base: `89b38ed734ab2d5c3d263bc24fbdb8c74e931af4`
- Verified Phase 1 squash reference: `c905373d2b1b6338153ee37b113c20b076d5f49a`
- Delta size: 70 commits

## Release freeze

Production was advancing because the `ubuntu` user crontab ran
`/usr/local/bin/hermes update --yes --force` every day at 03:00 UTC.  Syslog
records the 2026-08-31 launch at 03:00:01 UTC; the updater log records the
autostash, 70-commit fast-forward, overlay restoration and gateway restart;
the Git reflog records the fast-forward at 03:00:07 UTC.

Only that cron entry was paused.  Its exact original content and the freeze
evidence are stored under `/home/ubuntu/.hermes/deployment-state/`.  The
gateway and unrelated services were not stopped.

## 70-commit audit

Classification totals:

- `NO_PHASE1_IMPACT`: 21
- `PHASE1_ADJACENT`: 49
- `PHASE1_CONFLICTING`: 0

The following commits are `NO_PHASE1_IMPACT` (browser/skill installation,
catalogue, contributor metadata, security-pattern and formatting changes):

`0f7981b8a1`, `7d87bab502`, `9d813c7352`, `972d812429`, `f449374f0c`,
`86dda3cd36`, `7e95b67ad8`, `d041ed7ab2`, `11c8c05dc3`, `6b290b81d5`,
`21e52c1fd4`, `d5fd2e9366`, `2a598aad1c`, `4fcbe2d59c`, `c49fa88b80`,
`bdb8b1603f`, `0af3d62e7b`, `1b2aaae1f8`, `70370e089c`, `70220e529a`,
`5cc1369fa2`.

The remaining 49 commits are `PHASE1_ADJACENT`:

`52e5e7c034`, `1762d3788c`, `989e48cc85`, `58523f284c`, `5bdaea64ed`,
`31dbc2493f`, `26350357d7`, `514707ff3e`, `a2af8405d1`, `222fda3d6b`,
`c49e2a496e`, `be92703788`, `532b2d8874`, `52637fee63`, `80764b6d39`,
`08c7879ca1`, `903c36b6d4`, `5247a6f07f`, `48a4201f40`, `c9b9b5e6c7`,
`80044bf385`, `f245765a6d`, `8a8aa850f1`, `77f5de6272`, `66666f6e2e`,
`73f8fb74e0`, `ef71f2cad8`, `dce2ecb8a9`, `4f22543509`, `9744fc0c99`,
`d63f996a75`, `1f2bd9e763`, `cbc67b939f`, `cc4b5ba1fc`, `8d567ccd22`,
`c0787c8ec1`, `14f40c0d6a`, `0bcad4519e`, `c992c6de4f`, `de4155b1bb`,
`027b339e79`, `f132f34ab8`, `892f756b8c`, `a6549922b8`, `19a59e9c93`,
`74f9c5d7e6`, `ad925a08da`, `ed3562bbbc`, `ff3835a630`.

Adjacency is limited to cron input normalization, TUI resume/todo state,
Telegram handler registration, compression/compaction lifecycle and durable
backoff, endpoint capability propagation, unattended approval policy,
delegation route capability, Photon read receipts, and durable group-chat
authority/replay.  None creates an unadmitted persistent reasoning producer
or changes logical-turn admission, turn-attempt ownership, occurrence
identity, recovery eligibility, or terminal-turn reopening rules.

The newest commit, `ff3835a630`, compacts the live Codex app-server thread
from session hygiene and `/compress`.  Both paths operate on the cached agent
inside the existing session and do not create a new reasoning turn.  The
change preserves the active agent rather than evicting it, records
compression cooldown/boundary state through existing SessionDB APIs, and does
not bypass the logical-turn lease held by the owning model turn.

## Overlay reconciliation

The tracked overlay fingerprint changed only because `hermes update`
autostashed it, fast-forwarded the underlying base, and reapplied it.  The
archived and current patches have exactly 71 added and 57 removed lines and
their normalized added/removed-line digest is identical:
`03f22653b14fdce8946907f7ef60d78bbdbf2713b434ffb9ec6ce8f2415de83b`.
Untracked path and content inventory hashes are unchanged.

Classification: `UNCHANGED_SEMANTICS_BASE_SHIFT`, with the required behavior
`ALREADY_PRESENT`/`SUPERSEDED_BY_NEWER_DESIGN` in this integration branch.
The old overlay must not be reapplied.  Signed `pa:` callbacks, HMAC constant-
time verification, initiating-user/chat/message binding, request/task/repo/
action/scope/expiry/version checks, durable grants and durable continuation
dispatch are provided by the tracked Phase 1 implementation.

## Integration result

Applying the verified Phase 1 squash delta to `ff3835a630` produced no
production-code conflict.  One test-only conflict in
`tests/agent/test_compression_review_76354.py` was resolved in favor of the
newer frozen-base compression timing contract.  No newer live functionality
was reverted.

## Permanent updater architecture

Keep the existing scheduled update cadence, but point it at a dedicated
staging checkout/ref.  The updater may fetch, build and test there, then write
an auditable candidate SHA.  Production `/usr/local/lib/hermes-agent` should
move only through an explicit promotion command that requires an immutable
SHA, validates ancestry/overlay state, quiesces importers, and records rollback
evidence.  The production checkout must not run `git pull` on a timer.
