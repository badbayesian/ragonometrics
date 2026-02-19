import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CacheInspectorTab } from "./CacheInspectorTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("CacheInspectorTab", () => {
  it("loads chat and structured cache inspection data", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/cache/chat/inspect")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            paper_path: "/app/papers/p1.pdf",
            question: "Q",
            query_normalized: "q",
            model: "gpt-5-nano",
            top_k: 10,
            cache_key: "abc",
            cache_context_hash: "def",
            selected_layer: "strict",
            cache_miss_reason: "",
            strict_hit: true,
            fallback_hit: false,
          },
        });
      }
      if (url.includes("/api/v1/cache/structured/inspect")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            paper_path: "/app/papers/p1.pdf",
            model: "gpt-5-nano",
            total_questions: 10,
            cached_questions: 9,
            missing_questions: 1,
            coverage_ratio: 0.9,
            missing_question_ids: ["A10"],
            rows: [],
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const onStatus = vi.fn();
    render(<CacheInspectorTab paperId="p1" model="gpt-5-nano" onStatus={onStatus} />);

    await userEvent.click(screen.getByRole("button", { name: "Inspect Chat Cache" }));
    await waitFor(() => {
      expect(screen.getAllByText("Strict hit").length).toBeGreaterThan(0);
    });

    await userEvent.click(screen.getByRole("button", { name: "Inspect Structured Cache" }));
    await waitFor(() => {
      expect(screen.getByText("90%")).toBeInTheDocument();
      expect(screen.getByText("9/10")).toBeInTheDocument();
    });
  });
});
