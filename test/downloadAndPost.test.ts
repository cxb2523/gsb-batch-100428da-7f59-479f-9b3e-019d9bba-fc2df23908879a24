import assert from "assert"
import fs from "fs"
import http from "http"
import os from "os"
import path from "path"
import { AddressInfo } from "net"
import { downloadAndPost } from "../src/scraper/downloadAndPost"
import { downloadImage } from "../src/utils"
import type { GetPostsPkg } from "../src/scraper/getLikes"
import type { PostPostsPkg } from "../src/scraper/postLikes"

const { test } = require("node:test")

function makeTempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "postlikesbot-test-"))
}

function removeDir(dir: string) {
  if (fs.existsSync(dir)) {
    for (const entry of fs.readdirSync(dir)) {
      fs.unlinkSync(path.join(dir, entry))
    }
    fs.rmdirSync(dir)
  }
}

function makePost(
  postUrl: string,
  reaction: "like" | "react" = "like"
): GetPostsPkg {
  return { postUrl, reaction, type: "photo" }
}

test("a failed download does not stop later posts from downloading and posting", async () => {
  const dir = makeTempDir()
  try {
    const posts = [
      makePost("https://facebook.test/post-1"),
      makePost("https://facebook.test/post-2"),
      makePost("https://facebook.test/post-3")
    ]
    const imageUrls: Record<string, string> = {
      "https://facebook.test/post-1": "https://cdn.test/1.png",
      "https://facebook.test/post-2": "https://cdn.test/2.png",
      "https://facebook.test/post-3": "https://cdn.test/3.png"
    }
    const attemptedDownloads: string[] = []
    const posted: PostPostsPkg[][] = []
    const invalidMarked: string[] = []
    const output: string[] = []

    const result = await downloadAndPost(posts, {
      imagesDir: dir,
      getImageUrl: async postUrl => imageUrls[postUrl],
      downloadImage: async (imageUrl, file) => {
        attemptedDownloads.push(imageUrl)
        if (imageUrl === "https://cdn.test/2.png") {
          throw new Error("simulated network failure")
        }
        fs.writeFileSync(file, "image bytes")
      },
      postImages: async toPost => {
        posted.push(toPost)
      },
      onInvalidImageUrl: postUrl => invalidMarked.push(postUrl),
      output: (text, type) => output.push(type + ": " + text)
    })

    assert.deepStrictEqual(attemptedDownloads, [
      "https://cdn.test/1.png",
      "https://cdn.test/2.png",
      "https://cdn.test/3.png"
    ])
    assert.strictEqual(result.downloaded.length, 2)
    assert.deepStrictEqual(
      result.downloaded.map(p => p.postUrl),
      ["https://facebook.test/post-1", "https://facebook.test/post-3"]
    )
    assert.deepStrictEqual(result.failures, [
      {
        postUrl: "https://facebook.test/post-2",
        reason: "simulated network failure"
      }
    ])
    assert.strictEqual(posted.length, 1)
    assert.deepStrictEqual(
      posted[0].map(p => p.postUrl),
      ["https://facebook.test/post-1", "https://facebook.test/post-3"]
    )
    assert.deepStrictEqual(invalidMarked, [])
  } finally {
    removeDir(dir)
  }
})

test("when every download fails, nothing is posted and 'Nothing to post' is reported", async () => {
  const dir = makeTempDir()
  try {
    const posts = [makePost("https://facebook.test/a"), makePost("https://facebook.test/b")]
    let postCalls = 0
    const output: string[] = []

    const result = await downloadAndPost(posts, {
      imagesDir: dir,
      getImageUrl: async () => "https://cdn.test/image.png",
      downloadImage: async () => {
        throw new Error("connection reset")
      },
      postImages: async () => {
        postCalls++
      },
      onInvalidImageUrl: () => {
        throw new Error("failed posts must not be marked as invalid image URLs")
      },
      output: text => output.push(text)
    })

    assert.strictEqual(postCalls, 0)
    assert.strictEqual(result.downloaded.length, 0)
    assert.strictEqual(result.failures.length, 2)
    assert.deepStrictEqual(
      result.failures.map(f => f.postUrl),
      ["https://facebook.test/a", "https://facebook.test/b"]
    )
    assert.ok(
      output.some(line => line.includes("Nothing to post")),
      "expected a 'Nothing to post' message, got: " + output.join(" | ")
    )
  } finally {
    removeDir(dir)
  }
})

test("a throwing image lookup is settled per post and leaves the post retryable", async () => {
  const dir = makeTempDir()
  try {
    const posts = [makePost("https://facebook.test/broken"), makePost("https://facebook.test/ok")]
    const output: string[] = []
    const posted: PostPostsPkg[][] = []
    let invalidMarked = 0

    const result = await downloadAndPost(posts, {
      imagesDir: dir,
      getImageUrl: async postUrl => {
        if (postUrl === "https://facebook.test/broken") {
          throw new Error("navigation timeout")
        }
        return "https://cdn.test/ok.png"
      },
      downloadImage: async (_imageUrl, file) => {
        fs.writeFileSync(file, "image bytes")
      },
      postImages: async toPost => {
        posted.push(toPost)
      },
      onInvalidImageUrl: () => {
        invalidMarked++
      },
      output: text => output.push(text)
    })

    assert.deepStrictEqual(result.failures, [
      { postUrl: "https://facebook.test/broken", reason: "navigation timeout" }
    ])
    assert.deepStrictEqual(
      result.downloaded.map(p => p.postUrl),
      ["https://facebook.test/ok"]
    )
    assert.strictEqual(posted.length, 1)
    assert.deepStrictEqual(
      posted[0].map(p => p.postUrl),
      ["https://facebook.test/ok"]
    )
    assert.strictEqual(invalidMarked, 0)
    assert.ok(
      output.some(
        line =>
          line.includes("https://facebook.test/broken") &&
          line.includes("navigation timeout")
      ),
      "expected the failed post URL and reason to be logged"
    )
  } finally {
    removeDir(dir)
  }
})

test("the round summary reports success and failure counts plus failed URLs", async () => {
  const dir = makeTempDir()
  try {
    const output: string[] = []
    await downloadAndPost(
      [
        makePost("https://facebook.test/one"),
        makePost("https://facebook.test/two"),
        makePost("https://facebook.test/three")
      ],
      {
        imagesDir: dir,
        getImageUrl: async postUrl =>
          postUrl.endsWith("two") ? null : "https://cdn.test/image.png",
        downloadImage: async (_imageUrl, file) => {
          if (file.endsWith("1.png")) {
            throw new Error("timed out")
          }
          fs.writeFileSync(file, "image bytes")
        },
        postImages: async () => {},
        output: text => output.push(text)
      }
    )

    const summary = output.find(line => line.startsWith("Download summary"))
    assert.ok(summary, "expected a download summary, got: " + output.join(" | "))
    assert.ok(summary!.includes("1 succeeded"), summary)
    assert.ok(summary!.includes("1 failed"), summary)
    assert.ok(summary!.includes("https://facebook.test/three"), summary)
  } finally {
    removeDir(dir)
  }
})

test("a failed post is not recorded as posted and is not marked as an invalid image", async () => {
  const dir = makeTempDir()
  try {
    const posts = [
      makePost("https://facebook.test/will-fail"),
      makePost("https://facebook.test/will-post")
    ]
    //stands in for the persisted post record
    const store: Record<string, { isPosted?: boolean; isInvalidImageURL?: boolean }> = {}
    const output: string[] = []
    let attempt = 0

    await downloadAndPost(posts, {
      imagesDir: dir,
      getImageUrl: async () => "https://cdn.test/image.png",
      downloadImage: async (_imageUrl, file) => {
        attempt++
        if (attempt === 1) {
          throw new Error("simulated network failure")
        }
        fs.writeFileSync(file, "image bytes")
      },
      postImages: async toPost => {
        for (const post of toPost) {
          store[post.postUrl] = { isPosted: true }
        }
      },
      onInvalidImageUrl: postUrl => {
        store[postUrl] = Object.assign({}, store[postUrl], {
          isInvalidImageURL: true
        })
      },
      output: text => output.push(text)
    })

    assert.deepStrictEqual(store["https://facebook.test/will-post"], {
      isPosted: true
    })
    assert.strictEqual(
      (store["https://facebook.test/will-fail"] || {}).isPosted,
      undefined,
      "a failed post must not be marked as posted"
    )
    assert.strictEqual(
      (store["https://facebook.test/will-fail"] || {}).isInvalidImageURL,
      undefined,
      "a failed post must not be marked as an invalid image so it can be retried"
    )
  } finally {
    removeDir(dir)
  }
})

function startServer(
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => void
): Promise<{ url: string; close: () => Promise<void> }> {
  return new Promise(resolve => {
    const server = http.createServer(handler)
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as AddressInfo).port
      resolve({
        url: `http://127.0.0.1:${port}/image.png`,
        close: () =>
          new Promise<void>((res, rej) =>
            server.close(err => (err ? rej(err) : res()))
          )
      })
    })
  })
}

test("downloadImage renames a completed temporary file into place", async () => {
  const dir = makeTempDir()
  const bytes = Buffer.from("complete image content")
  const server = await startServer((_req, res) => {
    res.writeHead(200, {
      "Content-Type": "image/png",
      "Content-Length": bytes.length
    })
    res.end(bytes)
  })
  const file = path.join(dir, "0.png")
  try {
    await downloadImage(server.url, file)
    assert.ok(fs.existsSync(file), "expected the target png to exist")
    assert.deepStrictEqual(fs.readFileSync(file), bytes)
    assert.ok(!fs.existsSync(file + ".tmp"), "temporary file should be gone")
  } finally {
    await server.close()
    removeDir(dir)
  }
})

test("downloadImage removes the temporary file and rejects on an HTTP error", async () => {
  const dir = makeTempDir()
  const server = await startServer((_req, res) => {
    res.writeHead(500, { "Content-Type": "text/plain" })
    res.end("boom")
  })
  const file = path.join(dir, "0.png")
  try {
    await assert.rejects(downloadImage(server.url, file))
    assert.ok(!fs.existsSync(file), "target png must not be created on failure")
    assert.ok(!fs.existsSync(file + ".tmp"), "temporary file must be removed")
  } finally {
    await server.close()
    removeDir(dir)
  }
})

test("downloadImage removes the temporary file when the connection drops mid-download", async () => {
  const dir = makeTempDir()
  const server = await startServer((_req, res) => {
    res.writeHead(200, {
      "Content-Type": "image/png",
      "Content-Length": 1000
    })
    res.write(Buffer.alloc(100))
    setTimeout(() => res.destroy(), 20)
  })
  const file = path.join(dir, "0.png")
  try {
    await assert.rejects(downloadImage(server.url, file))
    assert.ok(!fs.existsSync(file), "target png must not be created on failure")
    assert.ok(!fs.existsSync(file + ".tmp"), "temporary file must be removed")
  } finally {
    await server.close()
    removeDir(dir)
  }
})
