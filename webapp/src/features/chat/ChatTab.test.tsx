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
      if (url.includes("/api/v1/chat/provenance-score")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            question: "What is the main research question?",
            score: 0.82,
            status: "high",
            warnings: [],
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
      expect(screen.getByText("Prov 82% (high)")).toBeInTheDocument();
    });

    expect(screen.getAllByText("What is the main research question?").length).toBeGreaterThanOrEqual(1);
  });

  it("queues additional prompts while one request is in-flight and runs them in order", async () => {
    let turnCalls = 0;
    let resolveFirstTurn: ((value: Response) => void) | null = null;
    const firstTurnPromise = new Promise<Response>((resolve) => {
      resolveFirstTurn = resolve;
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/chat/history")) {
        return Promise.resolve(jsonResponse({ ok: true, data: { rows: [], count: 0 } }));
      }
      if (url.includes("/api/v1/chat/suggestions")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            data: { paper_id: "p1", paper_title_hint: "Paper", questions: ["First question", "Second question"] },
          })
        );
      }
      if (url.includes("/api/v1/chat/turn")) {
        turnCalls += 1;
        const body = JSON.parse(String(init?.body || "{}")) as { question?: string };
        if (turnCalls === 1) {
          expect(body.question).toBe("First question");
          return firstTurnPromise;
        }
        expect(body.question).toBe("Second question");
        return Promise.resolve(
          jsonResponse({
            ok: true,
            data: {
              answer: "Second answer complete.",
              citations: [],
              retrieval_stats: { method: "local" },
              cache_hit: false,
              model: "gpt-5-nano",
            },
          })
        );
      }
      if (url.includes("/api/v1/chat/provenance-score")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            data: {
              paper_id: "p1",
              question: "q",
              score: 0.7,
              status: "medium",
              warnings: [],
            },
          })
        );
      }
      return Promise.resolve(jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <ChatTab
        csrfToken="csrf"
        paperId="p1"
        paperName="Paper.pdf"
        paperPath="/app/papers/Paper.pdf"
        model="gpt-5-nano"
        queuedQuestion=""
        onQuestionConsumed={() => undefined}
        onStatus={() => undefined}
        onOpenViewer={() => undefined}
      />
    );

    const textarea = await screen.findByPlaceholderText("Ask a question about this paper");
    await userEvent.type(textarea, "First question");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(turnCalls).toBe(1);
    });

    await userEvent.type(textarea, "Second question");
    await userEvent.click(screen.getByRole("button", { name: "Queue Ask" }));

    expect(screen.getByText("Queued questions (1)")).toBeInTheDocument();
    expect(turnCalls).toBe(1);

    resolveFirstTurn?.(
      jsonResponse({
        ok: true,
        data: {
          answer: "First answer complete.",
          citations: [],
          retrieval_stats: { method: "local" },
          cache_hit: false,
          model: "gpt-5-nano",
        },
      })
    );

    await waitFor(() => {
      expect(turnCalls).toBe(2);
    });
    await waitFor(() => {
      expect(screen.getByText("Second answer complete.")).toBeInTheDocument();
    });
  });
});
