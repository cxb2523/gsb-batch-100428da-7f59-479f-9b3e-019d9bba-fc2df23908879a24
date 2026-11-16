import path from "path"
import type { GetPostsPkg } from "./getLikes"
import type { PostPostsPkg } from "./postLikes"

export type ConsoleOutputType =
  | "info"
  | "error"
  | "loading"
  | "success"
  | "settings"
  | "sadtimes"
  | "startstop"

export interface DownloadFailure {
  postUrl: string
  reason: string
}

export interface DownloadAndPostResult {
  /** Posts whose image was downloaded completely and can be posted. */
  downloaded: PostPostsPkg[]
  /** Posts whose image lookup or download threw. These are left unmarked so the next run retries them. */
  failures: DownloadFailure[]
  /** Posts that are intentionally skipped because there is no image URL (videos/links). */
  invalidImageUrls: string[]
}

export interface DownloadAndPostDeps {
  imagesDir: string
  getImageUrl: (postUrl: string) => Promise<string | null | undefined>
  downloadImage: (imageUrl: string, file: string) => Promise<void>
  postImages: (posts: PostPostsPkg[]) => Promise<void>
  /** Called only when a post is confirmed not to have an image, never when lookup/download fails. */
  onInvalidImageUrl?: (postUrl: string) => void
  output?: (text: string, type: ConsoleOutputType) => void
  /** Lets the caller abort a round, e.g. when the user pressed stop. */
  shouldAbort?: () => boolean
}

/**
 * Downloads every post's image independently, then posts only the ones that
 * downloaded successfully. A single failing post never interrupts the round.
 */
export async function downloadAndPost(
  posts: GetPostsPkg[],
  deps: DownloadAndPostDeps
): Promise<DownloadAndPostResult> {
  const output = deps.output || (() => {})
  const downloaded: PostPostsPkg[] = []
  const failures: DownloadFailure[] = []
  const invalidImageUrls: string[] = []
  let aborted = false

  for (const post of posts) {
    if (deps.shouldAbort && deps.shouldAbort()) {
      aborted = true
      break
    }

    const reactionText = post.reaction === "like" ? "liked" : "reacted to"
    const postTypeText = post.type === "photo" ? "photo" : "image in post"
    output(
      `Downloading ${reactionText} ${postTypeText} at ${post.postUrl}`,
      "loading"
    )

    try {
      const imageUrl = await deps.getImageUrl(post.postUrl)
      if (!imageUrl) {
        invalidImageUrls.push(post.postUrl)
        if (deps.onInvalidImageUrl) {
          deps.onInvalidImageUrl(post.postUrl)
        }
        output(
          "Couldn't find the image URL (the post might not be an image, so it's safe to ignore this)",
          "sadtimes"
        )
        continue
      }

      const file = path.join(
        deps.imagesDir,
        downloaded.length.toString() + ".png"
      )
      await deps.downloadImage(imageUrl, file)
      downloaded.push({
        postUrl: post.postUrl,
        reaction: post.reaction,
        file
      })
      output("Downloaded image successfully", "info")
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      failures.push({ postUrl: post.postUrl, reason })
      output(
        `Failed to download image for post ${post.postUrl}: ${reason}`,
        "error"
      )
      // Deliberately do not mark the post as posted or as an invalid image URL,
      // so that it is picked up again on the next run.
    }
  }

  if (aborted) {
    return { downloaded, failures, invalidImageUrls }
  }

  try {
    if (downloaded.length > 0) {
      output("Preparing to post images", "loading")
      await deps.postImages(downloaded)
    } else {
      output("Nothing to post", "info")
    }
  } finally {
    //the round summary always closes the round, even if posting itself fails
    output(
      buildSummary(downloaded.length, failures, invalidImageUrls.length),
      failures.length > 0 ? "sadtimes" : "info"
    )
  }

  return { downloaded, failures, invalidImageUrls }
}

function buildSummary(
  succeededCount: number,
  failures: DownloadFailure[],
  skippedCount: number
): string {
  const skippedText =
    skippedCount > 0 ? `, ${skippedCount} skipped (no image found)` : ""
  if (failures.length === 0) {
    return `Download summary: ${succeededCount} succeeded, 0 failed${skippedText}`
  }
  const failedList = failures
    .map(failure => `${failure.postUrl} (${failure.reason})`)
    .join(", ")
  return `Download summary: ${succeededCount} succeeded, ${
    failures.length
  } failed${skippedText}. Failed posts: ${failedList}`
}
