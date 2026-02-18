# Web Button and Endpoint Matrix

| UI Surface | Action | Endpoint | Expected Outcome |
|---|---|---|---|
| Auth | Login | `POST /api/v1/auth/login` | Session and CSRF cookies set; user shown in header |
| Auth | Create Account | `POST /api/v1/auth/register` | New user created; optional alert email sent |
| Auth | Forgot Password | `POST /api/v1/auth/forgot-password` | Reset flow accepted with non-enumerating response |
| Auth | Reset Password | `POST /api/v1/auth/reset-password` | Password updated for valid reset token |
| Auth | Logout | `POST /api/v1/auth/logout` | Session revoked; login screen shown |
| Top Bar | Select Project | `POST /api/v1/projects/{project_id}/select` | Session project context switched; papers and persona options refreshed |
| Top Bar | Select Persona | `POST /api/v1/projects/{project_id}/personas/{persona_id}/select` | Session persona context switched |
| Paper Finder | Load papers | `GET /api/v1/papers` | Dedupe-aware title options displayed with active filters preserved |
| Chat | Ask | `POST /api/v1/chat/turn` | User+assistant turn appended; answer metadata shown |
| Chat | Ask (Stream) | `POST /api/v1/chat/turn-stream` | Incremental response updates and terminal done event |
| Chat | Provenance Score | `POST /api/v1/chat/provenance-score` | Deterministic provenance score and warnings shown on assistant messages |
| Chat | Clear History | `DELETE /api/v1/chat/history` | User+paper scoped history rows removed |
| Chat | Suggestions | `GET /api/v1/chat/suggestions` | Deterministic starter prompts shown |
| Chat | History Load | `GET /api/v1/chat/history` | Server-side user+paper scoped timeline restored |
| Structured | Refresh Cache | `GET /api/v1/structured/answers` | Cached-answer table updated |
| Structured | Generate Missing | `POST /api/v1/structured/generate-missing` | Missing rows generated and cached |
| Structured | Export JSON | `POST /api/v1/structured/export` | JSON bundle downloaded |
| Structured | Export PDF | `POST /api/v1/structured/export` | PDF downloaded (or typed error) |
| OpenAlex Metadata | Refresh | `GET /api/v1/openalex/metadata` | Metadata cards updated |
| OpenAlex Metadata | Apply Manual Link | `POST /api/v1/openalex/metadata/manual-link` | Manual OpenAlex work mapping saved for selected paper |
| Citation Network | Reload | `GET /api/v1/openalex/citation-network` | Graph + row lists updated (supports `n_hops`, default `1`, returns cache status metadata) |
| Citation Network | Reset | `GET /api/v1/openalex/citation-network` | Restores controls (`10/10/1`), resets graph viewport, and reloads immediately |
| Usage | Refresh | `GET /api/v1/usage/summary`, `GET /api/v1/usage/by-model`, `GET /api/v1/usage/recent` | Summary cards and tables updated |
| Cache Inspector | Inspect Chat Cache | `GET /api/v1/cache/chat/inspect` | Strict/fallback/miss diagnostics, keys, retrieval preview |
| Cache Inspector | Inspect Structured Cache | `GET /api/v1/cache/structured/inspect` | Per-question cache coverage and missing IDs |
| Compare | Similar papers | `GET /api/v1/compare/similar-papers` | Topic-assisted paper suggestions for selected seed paper |
| Compare | Build Matrix (Cache Only) | `POST /api/v1/compare/runs` | Saved comparison run with cached/missing matrix cells |
| Compare | Fill Missing | `POST /api/v1/compare/runs/{comparison_id}/fill-missing` | Missing/failed cells are generated and persisted |
| Compare | Export JSON/CSV | `POST /api/v1/compare/runs/{comparison_id}/export` | Downloadable comparison payload in JSON or CSV |

Notes:
- Every mutating request requires CSRF token header.
- Citation-network default `n_hops` is `1` in current web behavior.
- Streamlit remains active and supported in parallel with web migration.
- Request-level mutation logging includes `request_id`, `paper_id`, and user context for audit/debug.
- End-to-end smoke check command: `python tools/web_smoke.py --base-url http://localhost:8590 --identifier <user> --password <pass>`.
