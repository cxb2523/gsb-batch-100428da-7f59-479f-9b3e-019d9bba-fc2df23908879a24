/**
 * runner.ts
 *
 * Low-level execution of the `crwl crawl` command via `node:child_process/spawn`.
 * Handles timeout, abort (Ctrl+C), streaming stdout to the UI, and collecting stderr.
 *
 * Interruptions (timeout / user abort) are NOT hard failures: the promise resolves
 * with everything collected so far and `interrupted` set to the reason that arrived
 * first, so callers can salvage partial output instead of losing it.
 */

import { spawn } from "node:child_process";
import { getVenvEnv } from "./resolve.js";

export type InterruptReason = "timeout" | "aborted";

export interface CrawlResult {
	stdout: string;
	stderr: string;
	exitCode: number | null;
	/** Set when the run was cut short — the reason that arrived first wins. */
	interrupted?: InterruptReason;
}

/**
 * Runs `crwl crawl <args>` with optional progress streaming to the TUI.
 *
 * @param crwlPath  absolute path to the crwl binary
 * @param args      CLI arguments (excluding "crwl" itself)
 * @param timeoutSec  maximum wait time in seconds
 * @param signal    AbortSignal from pi (e.g. when the user presses Esc)
 * @param onUpdate  callback for live UI updates
 */
export async function runCrawl(
	crwlPath: string,
	args: string[],
	timeoutSec: number,
	signal: AbortSignal | undefined,
	onUpdate: ((update: { content: Array<{ type: "text"; text: string }> }) => void) | undefined,
	baseDirectory?: string,
): Promise<CrawlResult> {
	return new Promise((resolve, reject) => {
		const env = getVenvEnv(crwlPath, baseDirectory);

		const proc = spawn(crwlPath, args, {
			stdio: ["ignore", "pipe", "pipe"],
			env: { ...env, PYTHONUNBUFFERED: "1" },
		});

		let stdout = "";
		let stderr = "";
		let interrupted: InterruptReason | undefined;
		let forceKillId: ReturnType<typeof setTimeout> | undefined;

		const clearTimers = () => {
			clearTimeout(timeoutId);
			if (forceKillId !== undefined) {
				clearTimeout(forceKillId);
				forceKillId = undefined;
			}
		};

		// SIGTERM first; if the process ignores it, SIGKILL within 5s so neither
		// the process nor the force-kill timer can linger.
		const killProc = () => {
			proc.kill("SIGTERM");
			forceKillId = setTimeout(() => proc.kill("SIGKILL"), 5000);
		};

		// First interruption wins — a later timeout/abort must not overwrite the reason.
		const interrupt = (reason: InterruptReason) => {
			if (interrupted) return;
			interrupted = reason;
			killProc();
		};

		// Automatically kill after the time limit is exceeded
		const timeoutId = setTimeout(() => interrupt("timeout"), timeoutSec * 1000);

		proc.stdout.on("data", (data: Buffer) => {
			const chunk = data.toString("utf-8");
			stdout += chunk;
			onUpdate?.({
				content: [
					{
						type: "text",
						text: `crawl4ai: ${stdout.length} chars collected…`,
					},
				],
			});
		});

		proc.stderr.on("data", (data: Buffer) => {
			stderr += data.toString("utf-8");
		});

		proc.on("error", (err) => {
			clearTimers();
			reject(err);
		});

		proc.on("close", (code) => {
			clearTimers();
			resolve({ stdout, stderr, exitCode: code, interrupted });
		});

		// React to cancellation by the user (Esc in pi)
		if (signal?.aborted) {
			interrupt("aborted");
		} else {
			signal?.addEventListener("abort", () => interrupt("aborted"), { once: true });
		}
	});
}
