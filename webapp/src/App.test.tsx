import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("App", () => {
  it("renders login view when unauthenticated", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return jsonResponse({ ok: false, error: { code: "unauthorized", message: "Authentication required." } }, 401);
      }
      return jsonResponse({ ok: false }, 400);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("Ragonometrics Web")).toBeInTheDocument();
      expect(screen.getByText("Login")).toBeInTheDocument();
    });
  });

  it("shows debug tabs only when debug mode is enabled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return jsonResponse({ ok: true, data: { username: "admin", csrf_token: "csrf-1" } });
      }
      if (url.includes("/api/v1/papers")) {
        return jsonResponse({
          ok: true,
          data: { papers: [{ paper_id: "p1", name: "Paper.pdf", path: "/app/papers/Paper.pdf" }] },
        });
      }
      return jsonResponse({ ok: true, data: {} });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("Structured Workstream")).toBeInTheDocument();
      expect(screen.getByText("OpenAlex Metadata")).toBeInTheDocument();
      expect(screen.getByText("Citation Network")).toBeInTheDocument();
      expect(screen.getByText("Usage")).toBeInTheDocument();
      expect(screen.getByText("Compare")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Debug: Off" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Help / How To" })).toBeInTheDocument();
    });
    expect(screen.queryByText("Workflow Cache")).not.toBeInTheDocument();
    expect(screen.queryByText("Cache Inspector")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Debug: Off" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Debug: On" })).toBeInTheDocument();
      expect(screen.getByText("Workflow Cache")).toBeInTheDocument();
      expect(screen.getByText("Cache Inspector")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Help / How To" }));
    expect(screen.getByRole("dialog", { name: "How To Use Ragonometrics Web" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "How To Use Ragonometrics Web" })).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Help / How To" }));
    await user.click(screen.getByTestId("howto-overlay"));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "How To Use Ragonometrics Web" })).not.toBeInTheDocument();
    });
  });

  it("dedupes paper finder options by normalized title", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return jsonResponse({ ok: true, data: { username: "admin", csrf_token: "csrf-1" } });
      }
      if (url.includes("/api/v1/papers")) {
        return jsonResponse({
          ok: true,
          data: {
            papers: [
              { paper_id: "p1", name: "Paper One.pdf", path: "/app/papers/p1.pdf", display_title: "Food Desert Impacts" },
              { paper_id: "p2", name: "Paper Two.pdf", path: "/app/papers/p2.pdf", display_title: "Food-Desert Impacts" },
              { paper_id: "p3", name: "Paper Three.pdf", path: "/app/papers/p3.pdf", display_title: "Another Paper" },
            ],
          },
        });
      }
      return jsonResponse({ ok: true, data: {} });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("Paper Finder")).toBeInTheDocument();
    });
    const options = Array.from(document.querySelectorAll("#paper-title-options option")).map((node) => node.getAttribute("value"));
    expect(options.filter((value) => value === "Food Desert Impacts").length).toBe(1);
    expect(options.filter((value) => value === "Food-Desert Impacts").length).toBe(0);
    expect(options.filter((value) => value === "Another Paper").length).toBe(1);
  });
});
