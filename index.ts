#!/usr/bin/env node

import express, { Request, Response } from "express";
import cors from "cors";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { createRequire } from "module";

const require = createRequire(import.meta.url);

function getServerVersion(): string {
  const packageJsonCandidates = ["./package.json", "../package.json"];

  for (const packageJsonPath of packageJsonCandidates) {
    try {
      const { version } = require(packageJsonPath) as { version?: unknown };
      if (typeof version === "string" && version.trim() !== "") {
        return version;
      }
    } catch {
      // Try next candidate path.
    }
  }

  return "0.0.0";
}

const SCRAPI_SERVER_VERSION = getServerVersion();

const PORT = process.env.PORT || 5000;
const SCRAPI_API_KEY = process.env.SCRAPI_API_KEY || "00000000-0000-0000-0000-000000000000";
const SCRAPI_SERVER_NAME = "ScrAPI MCP Server";
const CORS_ALLOWED_ORIGINS = (process.env.CORS_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((origin) => origin.trim())
  .filter((origin) => origin.length > 0);

const app = express();

app.use(
  cors({
    origin: (origin, callback) => {
      const allowedOrigin = origin ? CORS_ALLOWED_ORIGINS.find((allowed) => allowed === origin) : undefined;
      callback(null, allowedOrigin ?? false);
    },
    exposedHeaders: ["mcp-session-id", "mcp-protocol-version"],
    allowedHeaders: ["Content-Type", "mcp-session-id", "mcp-protocol-version", "Authorization"],
    methods: ["GET", "POST", "OPTIONS"],
    preflightContinue: false,
  }),
);

app.use(express.json());

// Define session configuration schema
export const configSchema = z.object({
  scrapiApiKey: z.string().optional().describe("ScrAPI API key for scraping. Leave empty for default limited usage."),
});

const BROWSER_COMMANDS_DESCRIPTION =
  "BROWSER COMMANDS: You can optionally provide browser commands to interact with the page before scraping (e.g., clicking buttons, filling forms, scrolling). " +
  "Provide commands as a JSON array string. Available commands:\n" +
  '- Click: {"click": "#buttonId"} - Click an element using CSS selector\n' +
  '- Input: {"input": {"input[name=\'email\']": "value"}} - Fill an input field\n' +
  '- Select: {"select": {"select[name=\'country\']": "USA"}} - Select from dropdown\n' +
  '- Scroll: {"scroll": 1000} - Scroll down (negative values scroll up)\n' +
  '- Wait: {"wait": 5000} - Wait milliseconds (max 15000)\n' +
  '- WaitFor: {"waitfor": "#elementId"} - Wait for element to appear\n' +
  '- JavaScript: {"javascript": "console.log(\'test\')"} - Execute custom JS\n' +
  'Example: [{"click": "#accept-cookies"}, {"wait": 2000}, {"input": {"input[name=\'search\']": "query"}}]';

const KNOWN_BROWSER_COMMAND_ACTIONS = ["click", "input", "select", "scroll", "wait", "waitfor", "javascript"] as const;
const MAX_WAIT_COMMAND_MS = 15000;
const UPSTREAM_RESPONSE_SNIPPET_LENGTH = 500;
const SCRAPI_REQUEST_TIMEOUT_MS = 300000;

// Validate parsed browser commands locally so callers get a precise,
// actionable error instead of an opaque upstream failure.
// Returns an error message, or null when every command is valid.
export function validateBrowserCommands(commands: unknown[]): string | null {
  const knownActions = KNOWN_BROWSER_COMMAND_ACTIONS.join(", ");

  for (let index = 0; index < commands.length; index++) {
    const position = `Browser command #${index + 1}`;
    const command = commands[index];

    if (command === null || typeof command !== "object" || Array.isArray(command)) {
      return `${position} must be an object containing one of the known action keys (${knownActions}).`;
    }

    const record = command as Record<string, unknown>;
    const hasKnownAction = KNOWN_BROWSER_COMMAND_ACTIONS.some((action) => action in record);
    if (!hasKnownAction) {
      return `${position} has no known action key. Expected one of: ${knownActions}.`;
    }

    if ("wait" in record) {
      const wait = record["wait"];
      if (typeof wait !== "number" || !Number.isFinite(wait)) {
        return `${position}: the "wait" field must be a number (milliseconds).`;
      }
      if (wait > MAX_WAIT_COMMAND_MS) {
        return `${position}: the "wait" field must not exceed ${MAX_WAIT_COMMAND_MS} ms (got ${wait}).`;
      }
    }

    if ("scroll" in record) {
      const scroll = record["scroll"];
      if (typeof scroll !== "number" || !Number.isFinite(scroll)) {
        return `${position}: the "scroll" field must be a number (pixels).`;
      }
    }
  }

  return null;
}

function truncateForError(text: string): string {
  return text.length > UPSTREAM_RESPONSE_SNIPPET_LENGTH
    ? `${text.slice(0, UPSTREAM_RESPONSE_SNIPPET_LENGTH)}…`
    : text;
}

interface ScrapeRequestBody {
  url: string;
  useBrowser: boolean;
  solveCaptchas: boolean;
  acceptDialogs: boolean;
  proxyType: string;
  responseFormat: string;
  browserCommands?: unknown[];
}

export const scrapeToolInputSchema = {
  url: z.string().url({ error: "Invalid URL" }).describe("The URL to scrape"),
  browserCommands: z
    .string()
    .optional()
    .describe("Optional JSON array of browser commands to execute before scraping. See tool description for available commands and format."),
};

// Parse configuration from query parameters
export function parseConfig(req: Request): Record<string, unknown> {
  const configParam = req.query["config"];
  if (typeof configParam !== "string") {
    return {};
  }
  try {
    const decoded = Buffer.from(configParam, "base64").toString("utf-8");
    const parsed: unknown = JSON.parse(decoded);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return {};
  } catch {
    return {};
  }
}

// Create MCP server with your tools
export default function createServer({ config }: { config: z.infer<typeof configSchema> }) {
  const server = new McpServer({
    name: SCRAPI_SERVER_NAME,
    version: SCRAPI_SERVER_VERSION,
  });

  server.registerTool(
    "scrape_url_html",
    {
      title: "Scrape URL and respond with HTML",
      description:
        "Use a URL to scrape a website using the ScrAPI service and retrieve the result as HTML. " +
        "Use this for scraping website content that is difficult to access because of bot detection, captchas or even geolocation restrictions. " +
        "The result will be in HTML which is preferable if advanced parsing is required.\n\n" +
        BROWSER_COMMANDS_DESCRIPTION,
      inputSchema: scrapeToolInputSchema,
    },
    async ({ url, browserCommands }) => await scrapeUrl(url, "HTML", config.scrapiApiKey || SCRAPI_API_KEY, browserCommands),
  );

  server.registerTool(
    "scrape_url_markdown",
    {
      title: "Scrape URL and respond with Markdown",
      description:
        "Use a URL to scrape a website using the ScrAPI service and retrieve the result as Markdown. " +
        "Use this for scraping website content that is difficult to access because of bot detection, captchas or even geolocation restrictions. " +
        "The result will be in Markdown which is preferable if the text content of the webpage is important and not the structural information of the page.\n\n" +
        BROWSER_COMMANDS_DESCRIPTION,
      inputSchema: scrapeToolInputSchema,
    },
    async ({ url, browserCommands }) => await scrapeUrl(url, "Markdown", config.scrapiApiKey || SCRAPI_API_KEY, browserCommands),
  );

  return server.server;
}

export async function scrapeUrl(url: string, format: "HTML" | "Markdown", apiKey: string, browserCommands?: string): Promise<CallToolResult> {
  const body: ScrapeRequestBody = {
    url,
    useBrowser: true,
    solveCaptchas: true,
    acceptDialogs: true,
    proxyType: "Residential",
    responseFormat: format,
  };

  // Parse and add browser commands if provided
  if (browserCommands && browserCommands.trim() !== "") {
    try {
      const commands = JSON.parse(browserCommands) as unknown;
      if (Array.isArray(commands)) {
        const validationError = validateBrowserCommands(commands);
        if (validationError !== null) {
          return {
            content: [
              {
                type: "text" as const,
                text: `Error: Invalid browser commands. ${validationError}`,
              },
            ],
            isError: true,
          };
        }
        body.browserCommands = commands;
      } else {
        return {
          content: [
            {
              type: "text" as const,
              text: "Error: Browser commands must be a JSON array.",
            },
          ],
          isError: true,
        };
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      return {
        content: [
          {
            type: "text" as const,
            text: `Error: Invalid browser commands format. ${errorMessage}`,
          },
        ],
        isError: true,
      };
    }
  }

  try {
    console.error(`Using ScrAPI on URL: ${url} with format: ${format}`);

    const response = await fetch("https://api.scrapi.tech/v1/scrape", {
      method: "POST",
      headers: {
        "User-Agent": `${SCRAPI_SERVER_NAME} - ${SCRAPI_SERVER_VERSION}`,
        "Content-Type": "application/json",
        "X-API-KEY": apiKey,
      },
      body: JSON.stringify(body),
      // ScrAPI can take up to 5 minutes to solve captchas and load dynamic pages
      signal: AbortSignal.timeout(SCRAPI_REQUEST_TIMEOUT_MS),
    });

    const data = await response.text();

    if (response.ok) {
      return {
        content: [
          {
            type: "text" as const,
            text: data,
            _meta: {
              mimeType: `text/${format.toLowerCase()}`,
            },
          },
        ],
      };
    }

    const snippet = truncateForError(data);
    let category: string;
    if (response.status === 401 || response.status === 403) {
      category = "authentication failed (check your ScrAPI API key)";
    } else if (response.status >= 400 && response.status < 500) {
      category = "request rejected by ScrAPI (invalid parameters)";
    } else if (response.status >= 500) {
      category = "ScrAPI server error";
    } else {
      category = "unexpected response from ScrAPI";
    }

    return {
      content: [
        {
          type: "text" as const,
          text: `Error: ScrAPI ${category} (HTTP ${response.status}). Response: ${snippet}`,
        },
      ],
      isError: true,
    };
  } catch (error) {
    console.error("Error calling API:", error);
    const errorMessage = error instanceof Error ? error.message : "Unknown error occurred";
    const isTimeout =
      error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    const text = isTimeout
      ? `Error: ScrAPI request timed out after ${SCRAPI_REQUEST_TIMEOUT_MS} ms. ${errorMessage}`
      : `Error: Network error while contacting ScrAPI. ${errorMessage}`;

    return {
      content: [
        {
          type: "text" as const,
          text,
        },
      ],
      isError: true,
    };
  }
}

app.all("/mcp", async (req: Request, res: Response) => {
  try {
    // Parse and validate configuration
    const rawConfig = parseConfig(req);
    const config = configSchema.parse({
      scrapiApiKey: rawConfig["scrapiApiKey"] ?? SCRAPI_API_KEY,
    });

    const server = createServer({ config });
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });

    // Clean up on request close
    res.on("close", () => {
      transport.close();
      server.close();
    });

    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error("Error handling ScrAPI MCP request:", error);

    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null,
      });
    }
  }
});

// Main function to start the server in the appropriate mode
async function main() {
  const transport = process.env.TRANSPORT || "stdio";

  if (transport === "http") {
    // Run in HTTP mode
    app.listen(PORT, () => {
      console.error(`ScrAPI MCP HTTP Server version ${SCRAPI_SERVER_VERSION} listening on port ${PORT}`);
    });
  } else {
    const scrapiApiKey = SCRAPI_API_KEY;

    // Create server with configuration
    const server = createServer({
      config: {
        scrapiApiKey,
      },
    });

    // Start receiving messages on stdin and sending messages on stdout
    const stdioTransport = new StdioServerTransport();
    await server.connect(stdioTransport);
    console.error(`ScrAPI MCP Server version ${SCRAPI_SERVER_VERSION} running in stdio mode`);
  }
}

// Start the server
main().catch((error) => {
  console.error("Server error:", error);
  process.exit(1);
});
