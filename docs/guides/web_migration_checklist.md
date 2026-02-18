# Web Migration Acceptance Checklist

Run this checklist before advancing migration scope.

## Functional Buttons

- [ ] Login/logout works with CSRF-protected POSTs.
- [ ] Create-account and forgot/reset-password flows work.
- [ ] Project/persona selector changes are reflected in session context.
- [ ] Chat `Ask` succeeds and returns answer text.
- [ ] Chat `Ask (Stream)` emits deltas and completes with `done`.
- [ ] Chat queue behavior works while request is in-flight.
- [ ] Chat evidence panel opens selected citation in Paper Viewer.
- [ ] Structured `Generate Missing` works for selected scope.
- [ ] Structured JSON export works (`compact` and `full`).
- [ ] Structured PDF export works (`compact` and `full`) without 5xx.
- [ ] OpenAlex Metadata tab loads for selected paper.
- [ ] OpenAlex manual link action (`Apply Link`) succeeds for selected paper.
- [ ] Citation Network tab loads center/references/citing.
- [ ] Citation Network auto-reloads after control edits and reset returns defaults (`10/10/1`).
- [ ] Usage tab refreshes summary, by-model, and recent rows.
- [ ] Compare tab can build run, fill missing, and export.
- [ ] Debug mode toggles Workflow Cache and Cache Inspector visibility.

## Error Discipline

- [ ] No generic 500s for expected failures (typed API errors only).
- [ ] `request_id` is present on error payloads.
- [ ] UI shows actionable status text for failed operations.

## Runtime Safety

- [ ] `docker compose ps` shows both `web` and `streamlit` healthy.
- [ ] Streamlit on `8585` remains functional and unchanged.
- [ ] Web on `8590` has no `WORKER TIMEOUT` during normal smoke tests.
