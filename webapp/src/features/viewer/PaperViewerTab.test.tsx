import { render, screen, waitFor } from "@testing-library/react";
import { PaperViewerTab } from "./PaperViewerTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("PaperViewerTab", () => {
  it("applies evidence highlight search phrase and word range when provided", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/papers/p1/notes")) {
        return jsonResponse({ ok: true, data: { rows: [] } });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <PaperViewerTab
        csrfToken="csrf"
        paperId="p1"
        paperTitle="Paper"
        onStatus={() => undefined}
        highlightRequest={{
          page: 3,
          terms: ["impulse response"],
          excerpt: "The impulse response shows a persistent positive effect.",
          startWord: 120,
          endWord: 168,
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Evidence focus: page 3, words 120-168/i)).toBeInTheDocument();
    });

    const frame = screen.getByTitle("Paper PDF Viewer");
    const src = frame.getAttribute("src") || "";
    expect(src).toContain("#page=3&search=");
    expect(decodeURIComponent(src)).toContain("The impulse response shows a persistent positive effect.");
  });
});
