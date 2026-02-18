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

  it("shows all parity tabs when authenticated", async () => {
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
      expect(screen.getByText("Workflow Cache")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Help / How To" })).toBeInTheDocument();
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
});
