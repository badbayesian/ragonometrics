# Web Button and Endpoint Matrix

| UI Surface | Action | Endpoint | Expected Outcome |
|---|---|---|---|
| Auth | Login | `POST /api/v1/auth/login` | Session and CSRF cookies set; user shown in header |
| Auth | Logout | `POST /api/v1/auth/logout` | Session revoked; login screen shown |
| Chat | Ask | `POST /api/v1/chat/turn` | User+assistant turn appended; answer metadata shown |
| Chat | Ask (Stream) | `POST /api/v1/chat/turn-stream` | Incremental response updates and terminal done event |
| Chat | Clear History | `DELETE /api/v1/chat/history` | User+paper scoped history rows removed |
| Chat | Suggestions | `GET /api/v1/chat/suggestions` | Deterministic starter prompts shown |
| Chat | History Load | `GET /api/v1/chat/history` | Server-side user+paper scoped timeline restored |
| Structured | Refresh Cache | `GET /api/v1/structured/answers` | Cached-answer table updated |
| Structured | Generate Missing | `POST /api/v1/structured/generate-missing` | Missing rows generated and cached |
| Structured | Export JSON | `POST /api/v1/structured/export` | JSON bundle downloaded |
| Structured | Export PDF | `POST /api/v1/structured/export` | PDF downloaded (or typed error) |
| OpenAlex Metadata | Refresh | `GET /api/v1/openalex/metadata` | Metadata cards updated |
| Citation Network | Reload | `GET /api/v1/openalex/citation-network` | Graph + row lists updated |
| Usage | Refresh | `GET /api/v1/usage/summary`, `GET /api/v1/usage/by-model`, `GET /api/v1/usage/recent` | Summary cards and tables updated |

Notes:
- Every mutating request requires CSRF token header.
- Streamlit remains active and supported in parallel with web migration.
