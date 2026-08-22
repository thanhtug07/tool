# Worker Log Concurrency Audit

## Executive Summary

The UI showed chunks running sequentially because the **event pipeline serialized parallel events**, not because the backend was serial.

**Root cause:** The CancellationToken stored only ONE message (last-write-wins). When 4 chunks fired events within a 500ms poll interval, only the last event survived. The Rust poller then forwarded that single event, making the UI show sequential execution.

**Fix:** Added a FIFO event queue to CancellationToken + updated the progress endpoint to return all queued events + updated Rust polling to drain and forward every event.

## Architecture: 4-Layer Analysis

| Layer | Component | Status Before | Status After |
|-------|-----------|---------------|--------------|
| 1 | Worker execution | PARALLEL (threading.Thread pools) | PARALLEL (unchanged) |
| 2 | Worker event emission | SERIAL (last-write-wins single message) | PARALLEL (FIFO event queue) |
| 3 | Rust polling + IPC | LOSSY (500ms poll + dedup) | CORRECT (drains all events) |
| 4 | Frontend log rendering | CORRECT (appends events) | CORRECT (unchanged) |

## Root Cause Detail

### Before Fix

Worker event flow:
```
Chunk 1 STT started  -> cancel.set_progress(0.5, chunk, msg1)
Chunk 2 STT started  -> cancel.set_progress(0.5, chunk, msg2)  # overwrites msg1
Chunk 3 STT started  -> cancel.set_progress(0.5, chunk, msg3)  # overwrites msg2
Chunk 4 STT started  -> cancel.set_progress(0.5, chunk, msg4)  # overwrites msg3
```

Rust polls at t=500ms: gets only msg4 -> emits 1 event
Frontend receives: only "Chunk 4 started"

### After Fix

Worker event flow:
```
Chunk 1 STT started  -> cancel.set_event(info, msg1)  # enqueued
Chunk 2 STT started  -> cancel.set_event(info, msg2)  # enqueued
Chunk 3 STT started  -> cancel.set_event(info, msg3)  # enqueued
Chunk 4 STT started  -> cancel.set_event(info, msg4)  # enqueued
```

Rust polls at t=500ms: drain_events() returns [msg1, msg2, msg3, msg4] -> emits 4 events
Frontend receives: all 4 chunk-started events in FIFO order

## Files Changed

| File | Change |
|------|--------|
| worker/src/core/job.py | Added _events list + set_event() + drain_events() to CancellationToken |
| worker/src/api/pipeline.py | on_event now calls set_event() instead of set_progress(); progress endpoint returns events array |
| src-tauri/src/services/worker_client.rs | Added ProgressEvent struct + events field to ProgressResponse |
| src-tauri/src/services/pipeline_runner.rs | Polling loop drains events array and forwards each via ctx.log() |

## Verification

- Worker import: OK
- Worker unit tests: 158/158 passed
- Rust tests: 203/203 passed
- TypeScript typecheck: clean pass

## Event Flow After Fix

```
Worker (Python)                    Rust                        Frontend
  |                                 |                             |
  | Chunk 1 STT started             |                             |
  | -> cancel.set_event()           |                             |
  | Chunk 2 STT started             |                             |
  | -> cancel.set_event()           |                             |
  | Chunk 3 STT started             |                             |
  | -> cancel.set_event()           |                             |
  |                                 |                             |
  | <- GET /v1/progress (poll)      |                             |
  | -> drain_events() [3 events]    |                             |
  | -> response.events = [...]      |                             |
  |                                 |                             |
  |                                 | ctx.log(msg1) -> emit       |
  |                                 | ctx.log(msg2) -> emit       |
  |                                 | ctx.log(msg3) -> emit       |
  |                                 |       Tauri IPC             |
  |                                 |              -> onJobLog    |
  |                                 |              -> setEntries  |
  |                                 |              -> DOM render  |
```

## Acceptance Criteria

- [x] Backend chunks run in parallel (threading.Thread pools)
- [x] Event pipeline preserves all events (FIFO queue)
- [x] Rust polling drains all events (no loss)
- [x] Frontend receives all events (onJobLog handler)
- [x] Live Log shows concurrent chunk activity
- [x] No duplicate events
- [x] No missing events
- [x] Event order preserved (FIFO)
- [x] No performance regression (non-blocking event queue)
- [x] All tests pass
