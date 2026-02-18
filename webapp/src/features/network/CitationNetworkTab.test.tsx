import { render, screen, waitFor } from "@testing-library/react";
import { CitationNetworkTab } from "./CitationNetworkTab";

vi.mock("vis-network/standalone", () => {
  class DataSet {
    items: unknown[];
    constructor(items: unknown[]) {
      this.items = items;
    }
  }
  class Network {
    on() {
      return undefined;
    }
    destroy() {
      return undefined;
    }
  }
  return { DataSet, Network };
});

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("CitationNetworkTab", () => {
  it("renders citation rows and center summary", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/openalex/citation-network")) {
        return jsonResponse({
          ok: true,
          data: {
            available: true,
            center: {
              id: "W1",
              openalex_url: "https://openalex.org/W1",
              title: "Center Paper",
              publication_year: 2025,
              cited_by_count: 12,
            },
            references: [
              {
                id: "W2",
                openalex_url: "https://openalex.org/W2",
                title: "Reference Paper",
                publication_year: 2021,
                cited_by_count: 22,
              },
            ],
            citing: [
              {
                id: "W3",
                openalex_url: "https://openalex.org/W3",
                title: "Citing Paper",
                publication_year: 2026,
                cited_by_count: 8,
              },
            ],
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CitationNetworkTab paperId="p1" onStatus={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByText(/Center: Center Paper/i)).toBeInTheDocument();
      expect(screen.getByText("Reference Paper")).toBeInTheDocument();
      expect(screen.getByText("Citing Paper")).toBeInTheDocument();
    });
  });
});
