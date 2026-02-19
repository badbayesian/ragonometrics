import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowCacheTab } from "./WorkflowCacheTab";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("WorkflowCacheTab", () => {
  it("loads runs, auto-selects latest run, and renders internals", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/workflow/runs?")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            runs: [
              { run_id: "run-new", status: "completed", papers_dir: "/app/papers/A.pdf", started_at: "", finished_at: "" },
              { run_id: "run-old", status: "completed", papers_dir: "/app/papers/A.pdf", started_at: "", finished_at: "" },
            ],
            count: 2,
            selected_run_id: "run-new",
          },
        });
      }
      if (url.includes("/api/v1/workflow/runs/run-new/steps")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            run: { run_id: "run-new", status: "completed", papers_dir: "/app/papers/A.pdf" },
            steps: [{ step: "prep", status: "completed", output: { status: "completed" }, metadata: {} }],
            count: 1,
            internals: [{ internal_step: "agentic_plan", label: "Agentic Plan", status: "completed", detail: "subquestions=3" }],
            internals_count: 1,
            include_internals: true,
            usage_by_step: { prep: { calls: 1 } },
          },
        });
      }
      if (url.includes("/api/v1/workflow/runs/run-old/steps")) {
        return jsonResponse({
          ok: true,
          data: {
            paper_id: "p1",
            run: { run_id: "run-old", status: "completed", papers_dir: "/app/papers/A.pdf" },
            steps: [{ step: "prep", status: "completed", output: { status: "completed" }, metadata: {} }],
            count: 1,
            internals: [],
            internals_count: 0,
            include_internals: true,
            usage_by_step: {},
          },
        });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onStatus = vi.fn();
    const user = userEvent.setup();

    render(<WorkflowCacheTab paperId="p1" onStatus={onStatus} />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Workflow Cache" })).toBeInTheDocument();
      expect(screen.getByText("Agentic Plan")).toBeInTheDocument();
      expect(screen.getByText("subquestions=3")).toBeInTheDocument();
    });

    const select = screen.getByRole("combobox");
    expect((select as HTMLSelectElement).value).toBe("run-new");
    await user.selectOptions(select, "run-old");
    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe("run-old");
    });
  });

  it("shows empty state when no runs are returned", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/workflow/runs?")) {
        return jsonResponse({ ok: true, data: { paper_id: "p1", runs: [], count: 0, selected_run_id: "" } });
      }
      return jsonResponse({ ok: false, error: { code: "not_found", message: "not found" } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowCacheTab paperId="p1" onStatus={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByText("No cached workflow runs found for this paper.")).toBeInTheDocument();
    });
  });
});
