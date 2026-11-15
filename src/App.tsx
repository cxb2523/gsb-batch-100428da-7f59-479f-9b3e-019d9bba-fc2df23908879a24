import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { ThemeProvider } from "./components/theme-provider";
import { ModeToggle } from "./components/mode-toggle";
import {
  describeError,
  modeLabel,
  readScrapeResponse,
  truncate,
} from "@/lib/submissions";
import type {
  CancelReason,
  StoredContent,
  Submission,
  SubmissionOutcome,
} from "@/lib/submissions";
import { SubmissionResult } from "@/components/submission-result";

const formSchema = z
  .object({
    url: z.string().url(),
    selector: z.string().optional(),
    mode: z.string({ message: "Please select a scrape mode" }),
    maxConcurrency: z.coerce.number().optional(),
    maxRequestsPerCrawl: z.coerce.number().optional(),
    proxyUrls: z.array(z.string()).optional(),
    crawlId: z.string().optional(),
    llmExtractionFields: z.string().optional(),
    llmExtractionMode: z.enum(["html", "text"]).default("html"),
  })
  .refine(
    (data) => {
      if (
        data.mode === "llmExtraction" &&
        data.llmExtractionFields === undefined
      ) {
        return false;
      }
      return true;
    },
    {
      message:
        "LLM Extraction Fields are required when LLM Extraction mode is selected.",
      path: ["llmExtractionFields"],
    }
  )
  .refine(
    (data) => {
      if (
        data.mode === "llmExtraction" &&
        data.llmExtractionMode === undefined
      ) {
        return false;
      }
      return true;
    },
    {
      message:
        "LLM Extraction Mode is required when LLM Extraction mode is selected.",
      path: ["llmExtractionMode"],
    }
  );

type ScrapeOption = {
  value: string;
  label: string | React.ReactNode;
  description: string;
  image: string;
  docsUrl?: string;
  remixUrl?: string;
};

const scrapeOptions: ScrapeOption[] = [
  {
    value: "llmExtraction",
    label: (
      <div className="flex">
        <h3 className="font-semibold">LLM Extraction</h3>
        <div>
          <span className="ml-2 rounded-md bg-primary px-1.5 py-0.5 text-xs leading-none text-secondary no-underline group-hover:no-underline">
            New
          </span>
        </div>
      </div>
    ),
    description:
      "Easily extract structured data from any web page using GPT from OpenAI or Claude from Anthropic.",
    image: "llmExtractor.png",
    remixUrl: "https://buildship.app/remix?template=openai-extract-hackernews",
    docsUrl: "https://docs.buildship.com/utility-nodes/llm-extract",
  },
  {
    value: "crawl",
    label: "Crawl",
    description:
      "Crawl a given web url and return the text content. Works great for sites that have multiple pages to scrape.",
    image: "crawler.png",
    docsUrl: "https://docs.buildship.com/utility-nodes/crawler",
    remixUrl: "https://buildship.app/remix?template=gpt-crawler",
  },
  {
    value: "static",
    label: "Static",
    description:
      "Scrape a given web url and return the text content. Works great for less dynamic sites that don't rely on javascript to load.",
    image: "static.png",
    docsUrl: "https://docs.buildship.com/utility-nodes/scrape-web-url",
    remixUrl: "https://buildship.app/remix?template=scrape-static-site",
  },
  {
    value: "dynamic",
    label: "Dynamic",
    description:
      "Scrape a given web url and return the text content. Works great for more complex sites that rely on javascript to load.",
    image: "dynamic.png",
    docsUrl: "https://docs.buildship.com/utility-nodes/scrape-web-url-dynamic",
    remixUrl: "https://buildship.app/remix?template=scrape-dynamic-site",
  },
];

async function readRejectionDetail(response: Response): Promise<string> {
  try {
    const body = await response.text();
    return body.trim() ? truncate(body.trim()) : "The server returned an empty body.";
  } catch (error) {
    return `The server response body could not be read: ${describeError(error)}`;
  }
}

export default function App() {
  const [content, setContent] = useState<StoredContent | null>(null);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [accordionOpen, setAccordionOpen] = useState(true);
  const [copied, setCopied] = useState(false);

  const nextSubmissionId = useRef(1);
  const activeRequest = useRef<{
    id: number;
    controller: AbortController;
  } | null>(null);

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      url: "",
    },
  });

  function settleSubmission(id: number, outcome: SubmissionOutcome) {
    setSubmissions((previous) =>
      previous.map((submission) =>
        submission.id === id ? { ...submission, outcome } : submission
      )
    );
  }

  const cancelActiveRequest = useCallback(
    (reason: CancelReason, detail: string) => {
      const active = activeRequest.current;
      if (!active) {
        return;
      }
      activeRequest.current = null;
      active.controller.abort();
      settleSubmission(active.id, {
        kind: "cancelled",
        reason,
        detail,
        finishedAt: Date.now(),
      });
    },
    []
  );

  async function onSubmit(values: z.infer<typeof formSchema>) {
    const workflowUrl = import.meta.env.VITE_BUILDSHIP_WORKFLOW;

    // Only one submission may own the result area: anything still in flight
    // is explicitly cancelled rather than silently ignored.
    cancelActiveRequest(
      "superseded",
      "A newer submission was started before this request finished."
    );

    const id = nextSubmissionId.current++;
    const mode = values.mode;
    const startedAt = Date.now();
    const controller = new AbortController();
    activeRequest.current = { id, controller };

    setSubmissions((previous) => [
      ...previous,
      {
        id,
        mode,
        url: values.url,
        startedAt,
        outcome: { kind: "pending" },
      },
    ]);

    const isStillActive = () =>
      activeRequest.current?.id === id && !controller.signal.aborted;

    if (!workflowUrl) {
      activeRequest.current = null;
      settleSubmission(id, {
        kind: "network-error",
        message:
          "No workflow endpoint is configured. Set VITE_BUILDSHIP_WORKFLOW to the deployed workflow URL.",
        finishedAt: Date.now(),
      });
      return;
    }

    try {
      const response = await fetch(workflowUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(values),
        signal: controller.signal,
      });

      if (!isStillActive()) {
        return;
      }

      if (!response.ok) {
        const detail = await readRejectionDetail(response);
        if (!isStillActive()) {
          return;
        }
        activeRequest.current = null;
        settleSubmission(id, {
          kind: "server-rejected",
          httpStatus: response.status,
          statusText: response.statusText,
          detail,
          finishedAt: Date.now(),
        });
        return;
      }

      let parsed: Awaited<ReturnType<typeof readScrapeResponse>>;
      try {
        parsed = await readScrapeResponse(mode, response);
      } catch (error) {
        if (!isStillActive()) {
          return;
        }
        activeRequest.current = null;
        settleSubmission(id, {
          kind: "malformed-response",
          httpStatus: response.status,
          message: `The workflow responded, but its body could not be read: ${describeError(
            error
          )}`,
          finishedAt: Date.now(),
        });
        return;
      }

      if (!isStillActive()) {
        return;
      }

      activeRequest.current = null;
      setContent({
        submissionId: id,
        mode,
        renderKind: parsed.renderKind,
        text: parsed.text,
        receivedAt: Date.now(),
      });
      settleSubmission(id, {
        kind: "success",
        httpStatus: response.status,
        finishedAt: Date.now(),
      });
      if (mode === "crawl" || mode === "llmExtraction") {
        setAccordionOpen(false);
      }
    } catch (error) {
      // A cancellation aborts the fetch; that outcome was already recorded by
      // the code that cancelled the request, so it must not be overwritten.
      if (!isStillActive()) {
        return;
      }
      activeRequest.current = null;
      settleSubmission(id, {
        kind: "network-error",
        message: describeError(error),
        finishedAt: Date.now(),
      });
    }
  }

  const copyToClipboard = () => {
    if (!content) {
      return;
    }
    navigator.clipboard.writeText(content.text);
    setCopied(true);
    setTimeout(() => {
      setCopied(false);
    }, 2000);
  };

  const watchedMode = form.watch("mode");

  useEffect(() => {
    const mode = form.getValues().mode;
    cancelActiveRequest(
      "mode-change",
      `The mode was switched to ${modeLabel(mode)} before this request finished.`
    );
    if (mode !== "crawl") {
      form.reset({
        url: form.getValues().url,
        selector: form.getValues().selector,
        mode: mode,
      });
    }
    // The effect intentionally reruns only when the selected mode changes; the
    // form instance and cancellation callback above are stable for its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchedMode]);

  const latestSubmission = submissions[submissions.length - 1];
  const isPending = latestSubmission?.outcome.kind === "pending";

  return (
    <ThemeProvider defaultTheme="dark" storageKey="vite-ui-theme">
      <div className="px-4 pb-8 pt-4 space-y-14 max-w-7xl mx-auto">
        <header className="border-b pb-2 flex justify-between items-center">
          <a href="https://buildship.com">
            <div className="flex items-center">
              <img src="logo.webp" alt="" width={35} />
              <h2 className="text-lg">
                BuildShip - Website Scraping Playground
              </h2>
            </div>
          </a>

          <ModeToggle />
        </header>
        <div className="md:flex gap-8 md:justify-center md:items-center bg-secondary p-8 rounded">
          <div className="lg:w-[500px] md:w-[240px]">
            <h1 className="text-4xl font-bold mb-4">
              Watch the full web scraping tutorial with{" "}
              <span className="bg-clip-text text-transparent bg-gradient-to-r from-gradient-start via-gradient-middle to-gradient-end">
                BuildShip
              </span>
            </h1>
          </div>
          <div className="flex-1">
            <iframe
              width="100%"
              height="350"
              src="https://www.youtube.com/embed/OJZzwcgjKW8?si=HCru14wFrHC33owG"
              title="YouTube video player"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              referrerPolicy="strict-origin-when-cross-origin"
              allowFullScreen
            ></iframe>
          </div>
        </div>
        <div>
          <h1 className="text-4xl font-bold mb-4">
            Try web scraping with{" "}
            <span className="bg-clip-text text-transparent bg-gradient-to-r from-gradient-start via-gradient-middle to-gradient-end">
              BuildShip
            </span>
          </h1>
          <div>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-8"
                autoComplete="off"
              >
                <div className="md:grid md:grid-cols-2 md:gap-10 block">
                  <div className="space-y-3">
                    <div>
                      <FormField
                        control={form.control}
                        name="url"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>URL</FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="https://buildship.com"
                              />
                            </FormControl>
                            <FormDescription>
                              The url to scrape or crawl.
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                    <div className="space-y-3">
                      <Accordion
                        type="single"
                        collapsible
                        value={accordionOpen ? "item-1" : ""}
                      >
                        <AccordionItem value="item-1">
                          <AccordionTrigger
                            onClick={() => setAccordionOpen(!accordionOpen)}
                          >
                            Options
                          </AccordionTrigger>
                          <AccordionContent>
                            <div className="space-y-3">
                              <FormField
                                control={form.control}
                                name="selector"
                                render={({ field }) => (
                                  <FormItem>
                                    <FormLabel>Selector</FormLabel>
                                    <FormControl>
                                      <Input
                                        {...field}
                                        placeholder="body, .content, #main, etc."
                                      />
                                    </FormControl>
                                    <FormDescription>
                                      Optional html selector to scrape.
                                    </FormDescription>
                                    <FormMessage />
                                  </FormItem>
                                )}
                              />
                              {form.getValues().mode === "crawl" && (
                                <div className="grid grid-cols-2 gap-3">
                                  <FormField
                                    control={form.control}
                                    name="maxConcurrency"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>Max Concurrency</FormLabel>
                                        <FormControl>
                                          <Input type="number" {...field} />
                                        </FormControl>
                                        <FormDescription>
                                          Number of crawls to run in parallel
                                          (max limit: 20
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />

                                  <FormField
                                    control={form.control}
                                    name="maxRequestsPerCrawl"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>
                                          Max Requests per Crawl
                                        </FormLabel>
                                        <FormControl>
                                          <Input type="number" {...field} />
                                        </FormControl>
                                        <FormDescription>
                                          Max requests to be executed per crawl
                                          (max limit: 50).
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />

                                  <FormField
                                    control={form.control}
                                    name="proxyUrls"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>Proxy URLs</FormLabel>
                                        <FormControl>
                                          <Input {...field} />
                                        </FormControl>
                                        <FormDescription>
                                          List of proxy urls to be used
                                          automatically by crawler for all
                                          connections.
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />

                                  <FormField
                                    control={form.control}
                                    name="crawlId"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>Crawl ID</FormLabel>
                                        <FormControl>
                                          <Input {...field} />
                                        </FormControl>
                                        <FormDescription>
                                          Use crawlId returned from previous
                                          crawling to continue crawling
                                          remaining urls.
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />
                                </div>
                              )}

                              {form.getValues().mode === "llmExtraction" && (
                                <div className="space-y-3">
                                  <FormField
                                    control={form.control}
                                    name="llmExtractionFields"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>Extraction Fields</FormLabel>
                                        <FormControl>
                                          <Input
                                            {...field}
                                            placeholder="title,price,url,description"
                                          />
                                        </FormControl>
                                        <FormDescription>
                                          Comma-separated list of fields to
                                          extract using LLM.
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />

                                  <FormField
                                    control={form.control}
                                    name="llmExtractionMode"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel>Extraction Mode</FormLabel>
                                        <FormControl>
                                          <Select
                                            {...field}
                                            defaultValue="html"
                                          >
                                            <SelectTrigger>
                                              <SelectValue placeholder="Select a mode" />
                                            </SelectTrigger>
                                            <SelectContent>
                                              <SelectItem value="html">
                                                HTML
                                              </SelectItem>
                                              <SelectItem value="text">
                                                Text
                                              </SelectItem>
                                            </SelectContent>
                                          </Select>
                                        </FormControl>
                                        <FormDescription>
                                          Select the LLM extraction mode: HTML,
                                          which preserves web page semantics, or
                                          Text, which provides a concise context
                                          without URLs, images, etc.
                                        </FormDescription>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />
                                </div>
                              )}
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      </Accordion>
                    </div>

                    <SubmissionResult
                      submissions={submissions}
                      content={content}
                      copied={copied}
                      onCopy={copyToClipboard}
                    />
                  </div>
                  <div className="space-y-3">
                    <FormField
                      control={form.control}
                      name="mode"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Select Scrape Mode</FormLabel>
                          <div className="space-2-4">
                            {scrapeOptions.map((option) => (
                              <label
                                key={option.value}
                                className={`block space-y-1 md:flex md:items-center md:space-x-4 cursor-pointer  rounded p-4 ${
                                  field.value === option.value
                                    ? "bg-secondary"
                                    : "bg-transparent"
                                }`}
                              >
                                <input
                                  type="radio"
                                  {...field}
                                  value={option.value}
                                  className="hidden"
                                />
                                <img
                                  src={option.image}
                                  alt={option.value}
                                  className="w-52 rounded"
                                />
                                <div>
                                  {typeof option.label === "string" ? (
                                    <h3 className="font-semibold">
                                      {option.label}
                                    </h3>
                                  ) : (
                                    <>{option.label}</>
                                  )}

                                  <p className="text-sm">
                                    {option.description}
                                  </p>
                                  <div className="space-x-2">
                                    {option.docsUrl && (
                                      <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        className="mt-3 text-xs"
                                        asChild
                                      >
                                        <a
                                          href={option.docsUrl}
                                          target="_blank"
                                        >
                                          Docs
                                          <ArrowUpRight className="ml-1 h-4 w-4" />
                                        </a>
                                      </Button>
                                    )}
                                    {option.remixUrl && (
                                      <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        className="mt-3 text-xs"
                                        title="Clone template in BuildShip"
                                        asChild
                                      >
                                        <a
                                          href={option.remixUrl}
                                          target="_blank"
                                        >
                                          Remix
                                          <ArrowUpRight className="ml-1 h-4 w-4" />
                                        </a>
                                      </Button>
                                    )}
                                  </div>
                                </div>
                              </label>
                            ))}
                          </div>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <div className="sticky bottom-0 bg-background py-3 z-10">
                      <Button type="submit" className="w-full">
                        {isPending
                          ? form.getValues().mode === "crawl"
                            ? "Crawling..."
                            : form.getValues().mode === "llmExtraction"
                            ? "Extracting..."
                            : "Scraping..."
                          : form.getValues().mode === "crawl"
                          ? "Crawl"
                          : form.getValues().mode === "llmExtraction"
                          ? "Extract"
                          : "Scrape"}
                      </Button>
                    </div>
                  </div>
                </div>
              </form>
            </Form>
          </div>
        </div>
      </div>
    </ThemeProvider>
  );
}
