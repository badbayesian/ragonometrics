# Web Migration Acceptance Checklist

Run this checklist before advancing migration scope.

## Functional Buttons

- [ ] Login/logout works with CSRF-protected POSTs.
- [ ] Chat `Ask` succeeds and returns answer text.
- [ ] Chat `Ask (Stream)` emits deltas and completes with `done`.
- [ ] Structured `Generate Missing` works for selected scope.
- [ ] Structured JSON export works (`compact` and `full`).
- [ ] Structured PDF export works (`compact` and `full`) without 5xx.
- [ ] OpenAlex Metadata tab loads for selected paper.
- [ ] Citation Network tab loads center/references/citing.
- [ ] Usage tab refreshes summary, by-model, and recent rows.

## Error Discipline

- [ ] No generic 500s for expected failures (typed API errors only).
- [ ] `request_id` is present on error payloads.
- [ ] UI shows actionable status text for failed operations.

## Runtime Safety

- [ ] `docker compose ps` shows both `web` and `streamlit` healthy.
- [ ] Streamlit on `8585` remains functional and unchanged.
- [ ] Web on `8590` has no `WORKER TIMEOUT` during normal smoke tests.

