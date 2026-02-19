import { ApiEnvelope } from "./types";

export async function api<T>(path: string, options: RequestInit = {}, csrfToken = ""): Promise<ApiEnvelope<T>> {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const res = await fetch(path, { ...options, headers, credentials: "include" });
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await res.json()) as ApiEnvelope<T>;
  }
  const bodyPreview = ((await res.text()) || "").replace(/\s+/g, " ").trim().slice(0, 240);
  const timeoutHint = res.status >= 500 ? " If this was long-running, retry once or use stream mode." : "";
  return {
    ok: false,
    error: {
      code: "bad_response",
      message: `Unexpected response (${res.status}) type=${contentType || "unknown"}${bodyPreview ? ` body=${bodyPreview}` : ""}.${timeoutHint}`,
    },
  };
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

