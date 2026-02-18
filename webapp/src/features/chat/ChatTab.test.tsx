import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatTab } from "./ChatTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ChatTab", () => {
  it("loads suggestions/history and appends ask response in chat timeline", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/chat/history")) {
        return jsonResponse({ ok: true, data: { rows: [], count: 0 } });
      }
      if (url.includes("/api/v1/chat/suggestions")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            paper_title_hint: "Paper",
            questions: ["What is the main research question?", "What is the dataset?"],
          },
        });
      }
      if (url.includes("/api/v1/chat/turn")) {
        expect(init?.method).toBe("POST");
        return jsonResponse({
          ok: true,
          data: {
            answer: "This paper studies structural breaks.",
            citations: [{ page: 1, start_word: 0, end_word: 20 }],
            retrieval_stats: { method: "local" },
            cache_hit: false,
            model: "gpt-5-nano",
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });

    vi.stubGlobal("fetch", fetchMock);

    const onStatus = vi.fn();
    render(
      <ChatTab
        csrfToken="csrf"
        paperId="p1"
        paperName="Paper.pdf"
        paperPath="/app/papers/Paper.pdf"
        model="gpt-5-nano"
        queuedQuestion=""
        onQuestionConsumed={() => undefined}
        onStatus={onStatus}
        onOpenViewer={() => undefined}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "What is the main research question?" })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "What is the main research question?" }));
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(screen.getByText("This paper studies structural breaks.")).toBeInTheDocument();
    });

    expect(screen.getAllByText("What is the main research question?").length).toBeGreaterThanOrEqual(1);
  });
});
