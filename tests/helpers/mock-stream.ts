/**
 * Helpers for feeding fabricated JSONL streams into runCrawl without any
 * real network access. These mocks stand in for the Spider API response in
 * tests only — they are not part of the production crawl path.
 */

export interface MockStreamOptions {
  /** Chunks to emit; each is delivered via one reader.read() call. */
  chunks: Uint8Array[];
  /** When set, the next read() after `throwAfterChunks` chunks rejects. */
  readError?: Error;
  throwAfterChunks?: number;
  ok?: boolean;
  status?: number;
  statusText?: string;
}

const encoder = new TextEncoder();

export function encode(text: string): Uint8Array {
  return encoder.encode(text);
}

/**
 * Split a full JSONL payload into chunks at arbitrary boundaries so tests
 * can exercise lines truncated across chunks.
 */
export function splitIntoChunks(text: string, size: number): Uint8Array[] {
  const bytes = encoder.encode(text);
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i += size) {
    chunks.push(bytes.subarray(i, i + size));
  }
  return chunks;
}

export function mockFetchResponse(options: MockStreamOptions): typeof fetch {
  const {
    chunks,
    readError,
    throwAfterChunks = 0,
    ok = true,
    status = 200,
    statusText = ok ? "OK" : "Error",
  } = options;

  let readIndex = 0;

  const reader = {
    read: async (): Promise<{ done: boolean; value: Uint8Array | undefined }> => {
      if (readError && readIndex >= throwAfterChunks) {
        throw readError;
      }
      if (readIndex >= chunks.length) {
        return { done: true, value: undefined };
      }
      return { done: false, value: chunks[readIndex++] };
    },
  };

  const body = { getReader: () => reader };

  return (async () =>
    ({
      ok,
      status,
      statusText,
      body,
    } as unknown as Response)) as unknown as typeof fetch;
}
