# Phase 1 full-suite failure evidence — `d75f078d68ff3389f0ae2db166fd90bd6b098679`

The canonical durable run is preserved at
`/home/ubuntu/.hermes/test-runs/phase1-d75f078d68-20260830T055743Z`.
It ran `scripts/run_tests.sh` from 2026-08-30T05:58:22Z to
2026-08-30T06:44:43Z and exited 1.  This report does not replace its log.

| Test | Full-suite failure evidence | Invariant | Classification |
| --- | --- | --- | --- |
| `TestS3IdleChargedFromLastProgress::test_silence_cannot_approach_double_idle_timeout` | At 4.3% of the run the expected fallback was returned, but host elapsed was `1.1602588450186886s`, above the test's `0.72s` cap.  The surrounding log says the last progress was `0.3s` ago and then reports normal cancellation at `0.4s` silence / `0.5s` total wait.  Under 10 eight-process suite-like repetitions it failed 9/10 solely on that outer elapsed cap (`0.7913`–`1.7998s`); isolated repetitions passed. | A progress report 50ms into a 400ms wait must leave only 50ms for the next wait slice, not reset the idle interval. | Test scheduler benchmark, not a semantic result failure. |
| `TestPrefetchServerRetainVisibility::test_timed_out_ops_are_dropped_not_repolled` | At 61.2% of the run the second prefetch took `0.432809416s`, above the test's `0.25s` cap.  The preceding warning confirms the first prefetch dropped one unresolved operation after its 0.3s budget.  Under concurrent suite-like repetitions 8/10 instead returned from the first `join(timeout=5)` before the daemon had been scheduled to clear `_pending_retain_ops`; isolated repetitions passed. | A timed-out server retain operation is evicted, and a later prefetch does not re-poll it. | Test thread-scheduling and wall-clock observation, not an eviction/re-poll semantic failure. |

The test changes accompanying this report observe the relevant state directly:

* compression records the successive `Future.result(timeout=...)` slices under
  a deterministic clock;
* Hindsight runs the prefetch target inline and verifies the status-poll count
  does not change after eviction.

Neither change alters production timeouts, production thread scheduling, or
the behavior being asserted.
