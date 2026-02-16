# Streamlit UI

Run the local UI:

```bash
ragonometrics ui
```

Notes:
- The app includes Chat and Usage tabs.
- Answers are concise and researcher-focused, and include provenance via the `Snapshots` expander.
- A post-answer math review pass can rewrite function/math notation into Markdown-friendly LaTeX for rendering.
- Paper author display uses layered lookup: OpenAlex, PDF metadata (`pdfinfo`), then first-page text parsing.
- Optional page snapshots require `pdf2image` + Poppler and benefit from `pytesseract` for highlight overlays.
- External metadata (OpenAlex with CitEc fallback) is shown in an expander.
