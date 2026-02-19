import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CitationNetworkTab } from "./CitationNetworkTab";

let lastEdges: any[] = [];

vi.mock("vis-network/standalone", () => {
  class DataSet {
    items: unknown[];
    constructor(items: unknown[]) {
      this.items = items;
      const first = (Array.isArray(items) && items.length > 0 ? items[0] : {}) as any;
      if (first && typeof first === "object" && ("from" in first || "to" in first)) {
        lastEdges = Array.isArray(items) ? [...items] : [];
      }
    }
  }
  class Network {
    on() {
      return undefined;
    }
    once() {
      return undefined;
    }
    setOptions() {
      return undefined;
    }
    fit() {
      return undefined;
    }
    focus() {
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
  beforeEach(() => {
    lastEdges = [];
  });

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
      expect(screen.getByText("Selected paper cites this paper")).toBeInTheDocument();
      expect(screen.getByText("Selected paper was cited by this paper")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(lastEdges.length).toBeGreaterThan(0);
    });

    const called = fetchMock.mock.calls.map((entry) => String(entry[0]));
    expect(called.some((url) => url.includes("n_hops=1"))).toBe(true);
    expect(lastEdges.some((edge) => String(edge.relation || "") === "references" && String(edge.arrows || "") === "to")).toBe(true);
    expect(lastEdges.some((edge) => String(edge.relation || "") === "cites" && String(edge.arrows || "") === "to")).toBe(true);
    expect(
      lastEdges.some(
        (edge) =>
          String(edge.relation || "") === "references" &&
          String(edge.from || "").startsWith("ref-") &&
          String(edge.to || "").startsWith("center-")
      )
    ).toBe(true);
    expect(
      lastEdges.some(
        (edge) =>
          String(edge.relation || "") === "cites" &&
          String(edge.from || "").startsWith("center-") &&
          String(edge.to || "").startsWith("citing-")
      )
    ).toBe(true);
  });

  it("debounces auto reload for control changes and collapses rapid edits", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/openalex/citation-network")) {
        return jsonResponse({
          ok: true,
          data: {
            available: true,
            center: { id: "W1", openalex_url: "https://openalex.org/W1", title: "Center Paper", publication_year: 2025, cited_by_count: 12 },
            references: [],
            citing: [],
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CitationNetworkTab paperId="p1" onStatus={() => undefined} />);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const maxRefsInput = screen.getByLabelText("Max references") as HTMLInputElement;
    fireEvent.change(maxRefsInput, { target: { value: "12" } });

    await vi.advanceTimersByTimeAsync(1500);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(600);
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    fireEvent.change(maxRefsInput, { target: { value: "13" } });
    fireEvent.change(maxRefsInput, { target: { value: "14" } });
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2);
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it("reset restores defaults and triggers immediate reload", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/openalex/citation-network")) {
        return jsonResponse({
          ok: true,
          data: {
            available: true,
            center: { id: "W1", openalex_url: "https://openalex.org/W1", title: "Center Paper", publication_year: 2025, cited_by_count: 12 },
            references: [],
            citing: [],
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CitationNetworkTab paperId="p1" onStatus={() => undefined} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Max references"), { target: { value: "21" } });
    fireEvent.change(screen.getByLabelText("Max citing"), { target: { value: "22" } });
    fireEvent.change(screen.getByLabelText("Hops"), { target: { value: "4" } });

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const urls = fetchMock.mock.calls.map((entry) => String(entry[0]));
    expect(urls.some((url) => url.includes("max_references=10") && url.includes("max_citing=10") && url.includes("n_hops=1"))).toBe(true);
  });
});
