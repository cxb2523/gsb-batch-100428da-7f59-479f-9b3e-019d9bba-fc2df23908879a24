import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import {
  describeError,
  outcomeDetail,
  readScrapeResponse,
  summarizeOutcome,
} from "@/lib/submissions";

type PendingRequest = {
  url: string;
  init: RequestInit;
  signal: AbortSignal | null;
  resolve: (response: Response) => void;
  reject: (error: unknown) => void;
};

/**
 * Stands in for the external BuildShip workflow. Every call is held until the
 * test settles it, so submissions can be raced, cancelled, or failed on cue.
 */
function installWorkflowMock(options: { honorAbort: boolean }) {
  const requests: PendingRequest[] = [];

  const fetchMock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      return new Promise<Response>((resolve, reject) => {
        const signal = init?.signal ?? null;
        requests.push({
          url: String(input),
          init: init ?? {},
          signal,
          resolve,
          reject,
        });

        signal?.addEventListener("abort", () => {
          if (options.honorAbort) {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          }
        });
      });
    }
  );

  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, requests };
}

function jsonResponse(body: unknown, status = 200, statusText = "OK") {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" },
  });
}

function renderApp() {
  return render(<App />);
}

function chooseMode(container: HTMLElement, mode: string) {
  const radio = container.querySelector<HTMLInputElement>(
    `input[type="radio"][value="${mode}"]`
  );
  if (!radio) {
    throw new Error(`Mode radio "${mode}" was not found`);
  }
  fireEvent.click(radio);
}

function fillUrl(value: string) {
  fireEvent.change(screen.getByLabelText("URL"), { target: { value } });
}

function submit() {
  const label = /^(Scrape|Crawl|Extract|Scraping\.\.\.|Crawling\.\.\.|Extracting\.\.\.)$/;
  fireEvent.click(screen.getByRole("button", { name: label }));
}

/** Settles a mocked workflow response inside `act` and flushes its microtasks. */
async function settle(action: () => void) {
  await act(async () => {
    action();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function currentOutcome(): HTMLElement {
  return screen.getByTestId("submission-outcome");
}

async function expectOutcomeReady() {
  await waitFor(() => {
    expect(currentOutcome().getAttribute("data-outcome")).not.toBe("pending");
  });
}

beforeEach(() => {
  vi.stubEnv("VITE_BUILDSHIP_WORKFLOW", "https://workflow.example.test/scrape");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("submission outcomes", () => {
  it("shows a success outcome and the static content field", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/static");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe("https://workflow.example.test/scrape");

    const body = JSON.parse(String(requests[0].init.body));
    expect(body).toMatchObject({
      url: "https://example.com/static",
      mode: "static",
    });

    await settle(() =>
      requests[0].resolve(jsonResponse({ content: "static page body" }, 200))
    );

    await expectOutcomeReady();
    expect(currentOutcome().getAttribute("data-outcome")).toBe("success");
    expect(currentOutcome().getAttribute("data-http-status")).toBe("200");
    expect(screen.getByTestId("result-content-value").textContent).toContain(
      "static page body"
    );
    expect(screen.getByTestId("result-content-value").dataset.renderKind).toBe(
      "text"
    );
  });

  it("reports a non-2xx rejection with the status code instead of clearing results", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/failure");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    await settle(() =>
      requests[0].resolve(
        new Response('{"error":"workflow unavailable"}', {
          status: 503,
          statusText: "Service Unavailable",
        })
      )
    );

    await expectOutcomeReady();
    expect(currentOutcome().getAttribute("data-outcome")).toBe(
      "server-rejected"
    );
    expect(currentOutcome().getAttribute("data-http-status")).toBe("503");
    expect(currentOutcome().textContent).toContain("503");
    expect(currentOutcome().textContent).toContain("Service Unavailable");
    expect(screen.queryByTestId("result-content-value")).toBeNull();
  });

  it("reports a network error in the result area", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/offline");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    await settle(() => requests[0].reject(new TypeError("Failed to fetch")));

    await expectOutcomeReady();
    expect(currentOutcome().getAttribute("data-outcome")).toBe("network-error");
    expect(currentOutcome().textContent).toContain("Failed to fetch");
  });

  it("cancels an in-flight request when the mode changes and ignores its late result", async () => {
    // The mock deliberately ignores AbortSignal, mimicking a response that was
    // already travelling back when the user switched modes.
    const { requests } = installWorkflowMock({ honorAbort: false });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/slow");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));

    chooseMode(container, "dynamic");

    await waitFor(() => {
      expect(currentOutcome().getAttribute("data-outcome")).toBe("cancelled");
    });
    expect(currentOutcome().textContent).toContain("Cancelled");
    expect(requests[0].signal?.aborted).toBe(true);

    await settle(() =>
      requests[0].resolve(jsonResponse({ content: "stale static result" }))
    );

    expect(currentOutcome().getAttribute("data-outcome")).toBe("cancelled");
    expect(screen.queryByTestId("result-content-value")).toBeNull();
    expect(screen.queryByText(/stale static result/)).toBeNull();
  });

  it("keeps only the newest submission as the current outcome when submits overlap", async () => {
    const { requests } = installWorkflowMock({ honorAbort: false });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/race");

    submit();
    await waitFor(() => expect(requests).toHaveLength(1));

    submit();
    await waitFor(() => expect(requests).toHaveLength(2));

    await waitFor(() => {
      expect(currentOutcome().getAttribute("data-outcome")).toBe("pending");
    });
    expect(currentOutcome().getAttribute("data-submission-id")).toBe("2");

    // The first request settles late and must not become the current outcome.
    await settle(() =>
      requests[0].resolve(jsonResponse({ content: "first result" }, 200))
    );

    expect(currentOutcome().getAttribute("data-submission-id")).toBe("2");
    expect(currentOutcome().getAttribute("data-outcome")).toBe("pending");

    await settle(() =>
      requests[1].resolve(jsonResponse({ content: "second result" }, 200))
    );

    await expectOutcomeReady();
    expect(currentOutcome().getAttribute("data-submission-id")).toBe("2");
    expect(currentOutcome().getAttribute("data-outcome")).toBe("success");
    expect(screen.getByTestId("result-content-value").textContent).toContain(
      "second result"
    );

    const history = screen.getAllByTestId("submission-history-item");
    expect(history).toHaveLength(1);
    expect(history[0].textContent).toContain("#1");
    expect(history[0].getAttribute("data-outcome")).toBe("cancelled");
    expect(history[0].textContent).toContain("superseded");
  });

  it("keeps the previous successful content and marks which submission it came from after a failure", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "static");
    fillUrl("https://example.com/works");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    await settle(() =>
      requests[0].resolve(jsonResponse({ content: "cached earlier content" }, 200))
    );

    await expectOutcomeReady();
    expect(screen.getByTestId("result-content-source").dataset.submissionId).toBe(
      "1"
    );

    fillUrl("https://example.com/breaks");
    submit();

    await waitFor(() => expect(requests).toHaveLength(2));
    await settle(() =>
      requests[1].resolve(
        new Response("upstream exploded", {
          status: 500,
          statusText: "Internal Server Error",
        })
      )
    );

    await waitFor(() => {
      expect(currentOutcome().getAttribute("data-outcome")).toBe(
        "server-rejected"
      );
    });

    const content = screen.getByTestId("result-content-value");
    expect(content.textContent).toContain("cached earlier content");
    expect(screen.getByTestId("result-content-source").dataset.submissionId).toBe(
      "1"
    );
    expect(screen.getByTestId("result-content-source").textContent).toContain(
      "earlier submission"
    );
  });

  it("renders crawl responses as JSON", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "crawl");
    fillUrl("https://example.com/crawl");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    await settle(() =>
      requests[0].resolve(jsonResponse({ pages: [{ title: "Home" }] }, 200))
    );

    await expectOutcomeReady();
    expect(currentOutcome().getAttribute("data-outcome")).toBe("success");
    const content = screen.getByTestId("result-content-value");
    expect(content.dataset.renderKind).toBe("json");
    expect(content.textContent).toContain("pages");
    expect(content.textContent).toContain("Home");
  });

  it("renders dynamic responses as plain text", async () => {
    const { requests } = installWorkflowMock({ honorAbort: true });
    const { container } = renderApp();

    chooseMode(container, "dynamic");
    fillUrl("https://example.com/dynamic");
    submit();

    await waitFor(() => expect(requests).toHaveLength(1));
    await settle(() =>
      requests[0].resolve(new Response("plain dynamic text", { status: 200 }))
    );

    await expectOutcomeReady();
    const content = screen.getByTestId("result-content-value");
    expect(content.dataset.renderKind).toBe("text");
    expect(content.textContent).toBe("plain dynamic text");
  });
});

describe("readScrapeResponse", () => {
  it("keeps the existing per-mode response contract", async () => {
    await expect(
      readScrapeResponse("static", jsonResponse({ content: "hello" }))
    ).resolves.toEqual({ text: "hello", renderKind: "text" });

    await expect(
      readScrapeResponse("dynamic", new Response("raw body"))
    ).resolves.toEqual({ text: "raw body", renderKind: "text" });

    const crawl = await readScrapeResponse("crawl", jsonResponse({ pages: [] }));
    expect(crawl.renderKind).toBe("json");
    expect(JSON.parse(crawl.text)).toEqual({ pages: [] });

    const extraction = await readScrapeResponse(
      "llmExtraction",
      jsonResponse({ title: "Hello" })
    );
    expect(extraction.renderKind).toBe("json");
    expect(JSON.parse(extraction.text)).toEqual({ title: "Hello" });
  });
});

describe("outcome descriptions", () => {
  it("distinguishes success, rejection, network failure and cancellation", () => {
    expect(
      summarizeOutcome({ kind: "success", httpStatus: 200, finishedAt: 1 })
    ).toContain("Success");
    expect(
      summarizeOutcome({
        kind: "server-rejected",
        httpStatus: 422,
        statusText: "Unprocessable Entity",
        finishedAt: 1,
      })
    ).toContain("422");
    expect(
      summarizeOutcome({
        kind: "network-error",
        message: "Failed to fetch",
        finishedAt: 1,
      })
    ).toContain("Failed to fetch");
    expect(
      summarizeOutcome({
        kind: "cancelled",
        reason: "mode-change",
        detail: "mode switched",
        finishedAt: 1,
      })
    ).toContain("Cancelled");
  });

  it("describes unknown thrown values without crashing", () => {
    expect(describeError(new Error("boom"))).toBe("boom");
    expect(describeError("plain")).toBe("plain");
    expect(
      outcomeDetail({
        kind: "server-rejected",
        httpStatus: 500,
        statusText: "Oops",
        detail: "body",
        finishedAt: 1,
      })
    ).toBe("body");
  });
});
