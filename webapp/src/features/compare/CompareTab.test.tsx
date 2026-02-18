import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompareTab } from "./CompareTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("CompareTab", () => {
  it("builds a cache-first comparison matrix and renders rows", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/compare/similar-papers")) {
        return jsonResponse({
          ok: true,
          data: {
            seed_paper_id: "p-seed",
            count: 1,
            rows: [
              {
                paper_id: "p-2",
                name: "paper-2.pdf",
                path: "/app/papers/paper-2.pdf",
                title: "Paper 2",
                authors: "A, B",
                openalex_url: "https://openalex.org/W2",
                score: 0.88,
              },
            ],
          },
        });
      }
      if (url.includes("/api/v1/compare/runs?")) {
        return jsonResponse({ ok: true, data: { rows: [], count: 0, limit: 50, offset: 0 } });
      }
      if (url.endsWith("/api/v1/compare/runs") && String(init?.method || "GET").toUpperCase() === "POST") {
        return jsonResponse({
          ok: true,
          data: {
            comparison_id: "cmp1",
            name: "Comparison",
            model: "gpt-5-nano",
            compute_mode: "cache_only",
            visibility: "shared",
            status: "ready",
            paper_ids: ["p-seed", "p-2"],
            questions: [{ id: "Q01", text: "What is the main research question?", normalized: "what is the main research question" }],
            summary: { total_cells: 2, cached_cells: 1, missing_cells: 1, generated_cells: 0, failed_cells: 0, ready_cells: 1 },
            created_by_username: "admin",
            created_at: "2026-02-18T00:00:00+00:00",
            updated_at: "2026-02-18T00:00:00+00:00",
            papers: [
              { paper_id: "p-seed", name: "seed.pdf", display_title: "Seed Paper" },
              { paper_id: "p-2", name: "paper-2.pdf", display_title: "Paper 2" },
            ],
            matrix: [
              {
                question_id: "Q01",
                question_text: "What is the main research question?",
                question_normalized: "what is the main research question",
                cells: [
                  { paper_id: "p-seed", question_id: "Q01", cell_status: "cached", answer: "A1", structured_fields: {} },
                  { paper_id: "p-2", question_id: "Q01", cell_status: "missing", answer: "", structured_fields: {} },
                ],
              },
            ],
            cells: [
              { paper_id: "p-seed", question_id: "Q01", cell_status: "cached", answer: "A1", structured_fields: {} },
              { paper_id: "p-2", question_id: "Q01", cell_status: "missing", answer: "", structured_fields: {} },
            ],
          },
        });
      }
      return jsonResponse({ ok: true, data: {} });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CompareTab csrfToken="csrf-1" paperId="p-seed" model="gpt-5-nano" onStatus={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Topic-Assisted Paper Selection")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText(/Paper 2/i));
    await user.click(screen.getByRole("button", { name: "Build Matrix (Cache Only)" }));

    await waitFor(() => {
      expect(screen.getByText("Comparison")).toBeInTheDocument();
      expect(screen.getByText("cached")).toBeInTheDocument();
      expect(screen.getByText("missing")).toBeInTheDocument();
    });
  });
});
