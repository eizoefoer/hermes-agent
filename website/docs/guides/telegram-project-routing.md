---
title: Automatic Telegram project routing
---

Automatic project routing is opt-in and disabled by default. It runs each authenticated Telegram input in an isolated project session while replying to the original chat and thread. It does not switch the globally active project or change Telegram topic bindings. Existing conversations keep their normal behavior while routing is disabled.

Register projects with `hermes project create` and set their primary folders first. Then configure an explicit list of eligible project IDs or slugs. Registry membership and aliases alone do not authorize a project. For example, adapt these registry slugs to your installation:

```yaml
platforms:
  telegram:
    extra:
      project_routing:
        enabled: true
        eligible_projects: [alpha, beta]
        aliases:
          alpha: [first-project]
          beta: [second-project]
        references: {} # slug -> exact authored repository, PR or task references
```

The equivalent `telegram.project_routing` block is also supported. Set `telegram.project_routing.enabled` to opt in, `telegram.project_routing.eligible_projects` to the allowed registry IDs or slugs, `telegram.project_routing.aliases` to a mapping of slugs to authored names, and `telegram.project_routing.references` to a mapping of slugs to exact authored repository, PR or task references. Paths come exclusively from the native project registry. A changed workspace requires a new isolated session; the gateway refuses to combine an old workspace association with a different folder.

Direct authored names, aliases and configured references, and durable replies to bot messages, select a project. Conflicting evidence or an unknown request produces one expiring inline project choice. Selecting it submits the original input, including attachments, rather than creating a replacement request. Quoted and forwarded text is not routing authority. Voice is transcribed before selection. Routing keeps individual text and media inputs separate, including album items; each item needs authored project context or a durable reply anchor. This preserves input identity and prevents captions for different projects from being merged.

`status`, `project status` and `all projects status` return up to sixteen eligible project pointers from native session state. They do not read transcripts or repository files, invoke a model, or expose mutation tools. Unknown work requires a project choice.

Project sessions are partitioned by bot, profile, sender and visibility. Private and group histories remain separate. Inputs for one project serialize through the native FIFO; different projects can run concurrently. A fresh project session receives repository handoff pointers instead of copied physical chat history. Compression follows native session lineage. Missing, ended or archived mappings get a fresh session.

Unanchored `/stop`, `/new`, `/reset`, `/compress` and `/model` retain physical-session semantics. A control replying to a persisted project bot message requires the original session and current generation. Stale controls are refused. Approval buttons and approval text remain bound to their existing physical action; choosing a project never approves an operation.

Eligibility and connector access are checked again at admission, claim, choice consumption and delivery. Removing eligibility fences pending execution and delivery. Queued work can recover after restart; started work with an unknown outcome remains paused under the native recovery rules. Recovery does not rerun it. Successful text sends finalize their delivery obligation before anchor persistence, so an anchor-write failure cannot by itself cause text redelivery; a missing anchor requires explicit project context or a new choice.
