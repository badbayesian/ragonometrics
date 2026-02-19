import { render, screen, waitFor } from "@testing-library/react";
import { UsageTab } from "./UsageTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("UsageTab", () => {
  it("auto-loads usage with all-sessions default scope", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/usage/summary")) {
        return jsonResponse({ ok: true, data: { calls: 5, input_tokens: 100, output_tokens: 45, total_tokens: 145 } });
      }
      if (url.includes("/api/v1/usage/by-model")) {
        return jsonResponse({ ok: true, data: { rows: [{ model: "gpt-5-nano", calls: 5, total_tokens: 145 }] } });
      }
      if (url.includes("/api/v1/usage/recent")) {
        return jsonResponse({
          ok: true,
          data: { rows: [{ created_at: "2026-02-18T00:00:00", model: "gpt-5-nano", operation: "answer", total_tokens: 30 }] },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });

    vi.stubGlobal("fetch", fetchMock);
    const onStatus = vi.fn();

    render(<UsageTab onStatus={onStatus} />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Usage" })).toBeInTheDocument();
      expect(screen.getAllByText("gpt-5-nano").length).toBeGreaterThanOrEqual(1);
    });

    const summaryCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/v1/usage/summary"));
    expect(String(summaryCall?.[0])).toContain("session_only=0");
  });
});
