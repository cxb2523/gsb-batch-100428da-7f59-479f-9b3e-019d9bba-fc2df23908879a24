import { beforeEach, describe, expect, mock, test } from "bun:test";
import type { INode, INodeExecutionData } from "n8n-workflow";
import { TickTickTrigger } from "../../nodes/TickTick/TickTickTrigger.node";
import { ENDPOINTS } from "../../nodes/TickTick/helpers/constants";

interface ApiCall {
	method: string;
	endpoint: string;
}

interface PollMockOptions {
	authentication?:
		| "tickTickTokenApi"
		| "tickTickOAuth2Api"
		| "tickTickSessionApi";
	parameters?: Record<string, unknown>;
	mode?: "manual" | "trigger";
	staticData?: Record<string, unknown>;
	apiResponses?: Record<string, unknown>;
}

function createPollContext(options: PollMockOptions = {}) {
	const {
		authentication = "tickTickTokenApi",
		parameters = {},
		mode = "trigger",
		staticData = {},
		apiResponses = {},
	} = options;

	const calls: ApiCall[] = [];
	const node: INode = {
		id: "trigger-test",
		name: "TickTick Trigger",
		type: "n8n-nodes-ticktick.tickTickTrigger",
		typeVersion: 1,
		position: [0, 0],
		parameters: {},
	};

	const handle = (
		method: string,
		url: string,
		returnFullResponse?: boolean,
	): unknown => {
		const endpoint = url
			.replace(/^https:\/\/api\.ticktick\.com\/api\/v2/, "")
			.replace(/^https:\/\/api\.ticktick\.com/, "");

		if (endpoint.startsWith("/user/signon")) {
			const body = {
				token: "mock-session-token",
				inboxId: "inbox",
				userId: "mock-user",
			};
			return returnFullResponse
				? { body, headers: { "set-cookie": ["t=mock-session-token; Path=/"] } }
				: body;
		}

		calls.push({ method, endpoint });
		const key = Object.keys(apiResponses).find((k) => endpoint.startsWith(k));
		return key ? apiResponses[key] : {};
	};

	const ctx = {
		getNode: mock(() => node),
		getMode: mock(() => mode),
		getNodeParameter: mock((name: string, fallback?: unknown) => {
			if (name === "authentication") return authentication;
			const v = parameters[name];
			return v !== undefined ? v : fallback;
		}),
		getWorkflowStaticData: mock(() => staticData),
		getCredentials: mock(async () => ({
			username: "test@example.com",
			password: "test",
		})),
		helpers: {
			httpRequest: mock(
				async (req: {
					method: string;
					url: string;
					returnFullResponse?: boolean;
				}) => handle(req.method, req.url, req.returnFullResponse),
			),
			httpRequestWithAuthentication: mock(
				async (_cred: string, req: { method: string; url: string }) =>
					handle(req.method, req.url),
			),
			returnJsonArray: mock((items: unknown[]): INodeExecutionData[] =>
				(items as Record<string, unknown>[]).map((json) => ({ json }))
			),
		},
		_calls: calls,
	};

	return ctx;
}

const trigger = new TickTickTrigger();

describe("TickTickTrigger.poll", () => {
	describe("first poll baseline", () => {
		test("returns null and writes lastCreatedTime when no prior state", async () => {
			const staticData: Record<string, unknown> = {};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "t1", projectId: "p1", createdTime: "2026-04-20T10:00:00.000+0000" },
								{ id: "t2", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);

			expect(result).toBeNull();
			expect(staticData.lastCreatedTime).toBe("2026-04-21T10:00:00.000+0000");
			expect(staticData.lastCreatedTaskId).toBe("t2");
		});

		test("baseline tie-breaks same createdTime by task id", async () => {
			const staticData: Record<string, unknown> = {};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "a", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
								{ id: "c", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
								{ id: "b", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);

			expect(result).toBeNull();
			expect(staticData.lastCreatedTime).toBe("2026-04-21T10:00:00.000+0000");
			expect(staticData.lastCreatedTaskId).toBe("c");
		});

		test("does not write a watermark when no tasks were returned", async () => {
			const staticData: Record<string, unknown> = {};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: { [ENDPOINTS.SYNC]: { syncTaskBean: { update: [], add: [] } } },
			});

			const result = await trigger.poll.call(ctx as never);

			expect(result).toBeNull();
			expect(staticData.lastCreatedTime).toBeUndefined();
			expect(staticData.lastCreatedTaskId).toBeUndefined();
		});
	});

	describe("subsequent polls", () => {
		test("emits only tasks newer than lastCreatedTime", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T12:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "old", projectId: "p1", createdTime: "2026-04-20T10:00:00.000+0000" },
								{ id: "new1", projectId: "p1", createdTime: "2026-04-20T15:00:00.000+0000" },
								{ id: "new2", projectId: "p1", createdTime: "2026-04-21T09:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);

			expect(result).not.toBeNull();
			const emitted = result![0].map((i) => i.json.id);
			expect(emitted).toEqual(["new1", "new2"]);
			expect(staticData.lastCreatedTime).toBe("2026-04-21T09:00:00.000+0000");
			expect(staticData.lastCreatedTaskId).toBe("new2");
		});

		test("returns null when no new tasks", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-25T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "old", projectId: "p1", createdTime: "2026-04-20T10:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).toBeNull();
		});
	});

	describe("flood protection", () => {
		const baseMs = Date.parse("2026-05-01T00:00:00.000Z");
		const formatTs = (offsetSeconds: number) => {
			const iso = new Date(baseMs + offsetSeconds * 1000).toISOString();
			// Match the +0000 suffix shape used elsewhere in these tests
			return iso.replace("Z", "+0000");
		};
		const makeTasks = (count: number) =>
			Array.from({ length: count }, (_, i) => ({
				id: `t${i}`,
				projectId: "p1",
				createdTime: formatTs(i),
			}));

		test("caps emission at 500 oldest, advances watermark to last emitted", async () => {
			const tasks = makeTasks(600);

			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-30T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: tasks,
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);

			expect(result).not.toBeNull();
			expect(result![0]).toHaveLength(500);
			const ids = result![0].map((i) => i.json.id);
			expect(ids[0]).toBe("t0");
			expect(ids[499]).toBe("t499");
			expect(staticData.lastCreatedTime).toBe(formatTs(499));
			expect(staticData.lastCreatedTaskId).toBe("t499");
		});

		test("drains a 600-task backlog across consecutive polls without duplicates", async () => {
			const tasks = makeTasks(600);
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-30T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: { syncTaskBean: { update: tasks, add: [] } },
				},
			});

			const emitted: string[] = [];
			const batchSizes: number[] = [];
			for (let poll = 0; poll < 5; poll++) {
				const result = await trigger.poll.call(ctx as never);
				if (result === null) break;
				batchSizes.push(result[0].length);
				emitted.push(...result[0].map((i) => i.json.id as string));
			}

			expect(batchSizes).toEqual([500, 100]);
			expect(emitted).toHaveLength(600);
			expect(new Set(emitted).size).toBe(600);
			expect(emitted).toEqual(tasks.map((t) => t.id));

			// Backlog drained: the next poll has nothing left to send.
			expect(await trigger.poll.call(ctx as never)).toBeNull();
		});

		test("drains a backlog larger than two batches", async () => {
			const tasks = makeTasks(1203);
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-30T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: { syncTaskBean: { update: [...tasks].reverse(), add: [] } },
				},
			});

			const emitted: string[] = [];
			const batchSizes: number[] = [];
			for (let poll = 0; poll < 10; poll++) {
				const result = await trigger.poll.call(ctx as never);
				if (result === null) break;
				batchSizes.push(result[0].length);
				emitted.push(...result[0].map((i) => i.json.id as string));
			}

			expect(batchSizes).toEqual([500, 500, 203]);
			expect(emitted).toHaveLength(1203);
			expect(new Set(emitted).size).toBe(1203);
			// Ordering is by createdTime then id, regardless of API order.
			expect(emitted).toEqual(tasks.map((t) => t.id));
		});
	});

	describe("same-millisecond tasks", () => {
		const sameTime = "2026-05-02T08:00:00.000+0000";

		test("emits every task sharing a createdTime exactly once across polls", async () => {
			const tasks = Array.from({ length: 12 }, (_, i) => ({
				id: `same-${String(i).padStart(2, "0")}`,
				projectId: "p1",
				createdTime: sameTime,
			}));

			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-05-01T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					// Feed the tasks in a shuffled order to prove ordering is ours.
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [tasks[7], tasks[0], tasks[11], tasks[5], ...tasks.slice(1, 5), ...tasks.slice(8, 11), tasks[6]],
							add: [],
						},
					},
				},
			});

			const first = await trigger.poll.call(ctx as never);
			expect(first).not.toBeNull();
			expect(first![0].map((i) => i.json.id)).toEqual(tasks.map((t) => t.id));
			expect(staticData.lastCreatedTime).toBe(sameTime);
			expect(staticData.lastCreatedTaskId).toBe("same-11");

			// Nothing left on the next poll.
			expect(await trigger.poll.call(ctx as never)).toBeNull();
		});

		test("continues past an equal-timestamp watermark with newer ids", async () => {
			// Simulates a watermark that landed mid-burst: the tasks sharing its
			// timestamp and sorting after it must still be emitted.
			const staticData: Record<string, unknown> = {
				lastCreatedTime: sameTime,
				lastCreatedTaskId: "same-03",
			};
			const tasks = Array.from({ length: 8 }, (_, i) => ({
				id: `same-${String(i).padStart(2, "0")}`,
				projectId: "p1",
				createdTime: sameTime,
			}));
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: { syncTaskBean: { update: tasks, add: [] } },
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			expect(result![0].map((i) => i.json.id)).toEqual([
				"same-04",
				"same-05",
				"same-06",
				"same-07",
			]);
			expect(staticData.lastCreatedTaskId).toBe("same-07");
		});

		test("splits an equal-timestamp burst across the 500 cap", async () => {
			const tasks = Array.from({ length: 700 }, (_, i) => ({
				id: `same-${String(i).padStart(4, "0")}`,
				projectId: "p1",
				createdTime: sameTime,
			}));
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-05-01T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: { syncTaskBean: { update: tasks, add: [] } },
				},
			});

			const emitted: string[] = [];
			for (let poll = 0; poll < 5; poll++) {
				const result = await trigger.poll.call(ctx as never);
				if (result === null) break;
				emitted.push(...result[0].map((i) => i.json.id as string));
			}

			expect(emitted).toHaveLength(700);
			expect(new Set(emitted).size).toBe(700);
			expect(emitted).toEqual(tasks.map((t) => t.id));
			expect(staticData.lastCreatedTaskId).toBe("same-0699");
		});

		test("upgrades a legacy timestamp-only watermark without dropping tasks", async () => {
			// Static data written by the previous implementation has no task id.
			// Its same-millisecond tasks may include ones never emitted, so the
			// whole millisecond is replayed rather than silently skipped.
			const staticData: Record<string, unknown> = {
				lastCreatedTime: sameTime,
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "same-01", projectId: "p1", createdTime: sameTime },
								{ id: "same-02", projectId: "p1", createdTime: sameTime },
								{ id: "later", projectId: "p1", createdTime: "2026-05-02T09:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			const ids = result![0].map((i) => i.json.id);
			expect(ids).toEqual(["same-01", "same-02", "later"]);
			expect(staticData.lastCreatedTaskId).toBe("later");
		});
	});

	describe("filter and watermark interaction", () => {
		test("a filtered-out newest task does not swallow matching older tasks", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: {
					event: "taskCreated",
					options: {
						projectIds: {
							project: [{ value: { mode: "id", value: "p1" } }],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "keep", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
								{ id: "drop-newest", projectId: "p2", createdTime: "2026-04-22T10:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			expect(result![0].map((i) => i.json.id)).toEqual(["keep"]);
			// The watermark follows the emitted task, not the dropped one.
			expect(staticData.lastCreatedTime).toBe("2026-04-21T10:00:00.000+0000");
			expect(staticData.lastCreatedTaskId).toBe("keep");
		});

		test("capped batch does not let unfiltered tasks past the watermark", async () => {
			// 600 matching tasks in p1, plus newer tasks in p2 that are filtered
			// out. The watermark must stop at the 500th matching task so the
			// remaining 100 are still delivered.
			const baseMs = Date.parse("2026-06-01T00:00:00.000Z");
			const fmt = (offset: number) =>
				new Date(baseMs + offset * 1000).toISOString().replace("Z", "+0000");
			const matching = Array.from({ length: 600 }, (_, i) => ({
				id: `m${String(i).padStart(3, "0")}`,
				projectId: "p1",
				createdTime: fmt(i),
			}));
			const filtered = [
				{ id: "x1", projectId: "p2", createdTime: fmt(10) },
				{ id: "x2", projectId: "p2", createdTime: fmt(999) },
			];

			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-05-31T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: {
					event: "taskCreated",
					options: {
						projectIds: {
							project: [{ value: { mode: "id", value: "p1" } }],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: { update: [...filtered, ...matching], add: [] },
					},
				},
			});

			const emitted: string[] = [];
			for (let poll = 0; poll < 5; poll++) {
				const result = await trigger.poll.call(ctx as never);
				if (result === null) break;
				emitted.push(...result[0].map((i) => i.json.id as string));
			}

			expect(emitted).toHaveLength(600);
			expect(emitted).toEqual(matching.map((t) => t.id));
			expect(staticData.lastCreatedTaskId).toBe("m599");
		});
	});

	describe("project filter", () => {
		test("excludes tasks from non-selected projects (V2)", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: {
					event: "taskCreated",
					options: {
						projectIds: {
							project: [{ value: { mode: "id", value: "p1" } }],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "keep", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
								{ id: "drop", projectId: "p2", createdTime: "2026-04-21T11:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			const ids = result![0].map((i) => i.json.id);
			expect(ids).toEqual(["keep"]);
		});
	});

	describe("assignee filter", () => {
		test("matches numeric assignee", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: {
					event: "taskCreated",
					options: {
						assigneeIds: {
							assignee: [{ value: { mode: "id", value: "12345" } }],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "match", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000", assignee: 12345 },
								{ id: "miss", projectId: "p1", createdTime: "2026-04-21T11:00:00.000+0000", assignee: 99999 },
								{ id: "none", projectId: "p1", createdTime: "2026-04-21T12:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			const ids = result![0].map((i) => i.json.id);
			expect(ids).toEqual(["match"]);
		});

		test("matches TaskAssignee object shape", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				parameters: {
					event: "taskCreated",
					options: {
						assigneeIds: {
							assignee: [{ value: { mode: "id", value: "12345" } }],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{
									id: "match",
									projectId: "p1",
									createdTime: "2026-04-21T10:00:00.000+0000",
									assignee: { userId: 12345, displayName: "Alice" },
								},
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			expect(result![0]).toHaveLength(1);
			expect(result![0][0].json.id).toBe("match");
		});
	});

	describe("manual mode", () => {
		test("returns single most-recent task and does not write static data", async () => {
			const staticData: Record<string, unknown> = {};
			const ctx = createPollContext({
				authentication: "tickTickSessionApi",
				mode: "manual",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.SYNC]: {
						syncTaskBean: {
							update: [
								{ id: "older", projectId: "p1", createdTime: "2026-04-20T10:00:00.000+0000" },
								{ id: "newest", projectId: "p1", createdTime: "2026-04-22T10:00:00.000+0000" },
							],
							add: [],
						},
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			expect(result![0]).toHaveLength(1);
			expect(result![0][0].json.id).toBe("newest");
			expect(staticData.lastCreatedTime).toBeUndefined();
		});
	});

	describe("V1 auth", () => {
		test("iterates explicitly-selected projects", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickTokenApi",
				parameters: {
					event: "taskCreated",
					options: {
						projectIds: {
							project: [
								{ value: { mode: "id", value: "p1" } },
								{ value: { mode: "id", value: "p2" } },
							],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.OPEN_V1_PROJECT_DATA("p1")]: {
						tasks: [
							{ id: "a", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
						],
					},
					[ENDPOINTS.OPEN_V1_PROJECT_DATA("p2")]: {
						tasks: [
							{ id: "b", projectId: "p2", createdTime: "2026-04-22T10:00:00.000+0000" },
						],
					},
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			const ids = result![0].map((i) => i.json.id).sort();
			expect(ids).toEqual(["a", "b"]);

			const endpoints = ctx._calls.map((c) => c.endpoint);
			expect(endpoints).toContain(ENDPOINTS.OPEN_V1_PROJECT_DATA("p1"));
			expect(endpoints).toContain(ENDPOINTS.OPEN_V1_PROJECT_DATA("p2"));
		});

		test("lists all projects when none selected", async () => {
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickTokenApi",
				parameters: { event: "taskCreated", options: {} },
				staticData,
				apiResponses: {
					[ENDPOINTS.OPEN_V1_PROJECT_DATA("p1")]: {
						tasks: [
							{ id: "a", projectId: "p1", createdTime: "2026-04-21T10:00:00.000+0000" },
						],
					},
					[ENDPOINTS.OPEN_V1_PROJECT]: [
						{ id: "p1", name: "P1", closed: false },
						{ id: "p2", name: "P2", closed: true },
					],
				},
			});

			const result = await trigger.poll.call(ctx as never);
			expect(result).not.toBeNull();
			const endpoints = ctx._calls.map((c) => c.endpoint);
			expect(endpoints).toContain(ENDPOINTS.OPEN_V1_PROJECT);
			expect(endpoints).toContain(ENDPOINTS.OPEN_V1_PROJECT_DATA("p1"));
			expect(endpoints).not.toContain(ENDPOINTS.OPEN_V1_PROJECT_DATA("p2"));
		});

		test("drains a same-timestamp backlog across polls", async () => {
			// V1 spreads one project's tasks across separate responses; the
			// composite watermark has to work the same way it does for V2.
			const shared = "2026-04-21T10:00:00.000+0000";
			const p1Tasks = Array.from({ length: 3 }, (_, i) => ({
				id: `p1-${i}`,
				projectId: "p1",
				createdTime: shared,
			}));
			const p2Tasks = Array.from({ length: 700 }, (_, i) => ({
				id: `p2-${String(i).padStart(3, "0")}`,
				projectId: "p2",
				createdTime: shared,
			}));
			const staticData: Record<string, unknown> = {
				lastCreatedTime: "2026-04-20T00:00:00.000+0000",
			};
			const ctx = createPollContext({
				authentication: "tickTickTokenApi",
				parameters: {
					event: "taskCreated",
					options: {
						projectIds: {
							project: [
								{ value: { mode: "id", value: "p1" } },
								{ value: { mode: "id", value: "p2" } },
							],
						},
					},
				},
				staticData,
				apiResponses: {
					[ENDPOINTS.OPEN_V1_PROJECT_DATA("p1")]: { tasks: p1Tasks },
					[ENDPOINTS.OPEN_V1_PROJECT_DATA("p2")]: { tasks: p2Tasks },
				},
			});

			const emitted: string[] = [];
			for (let poll = 0; poll < 5; poll++) {
				const result = await trigger.poll.call(ctx as never);
				if (result === null) break;
				emitted.push(...result[0].map((i) => i.json.id as string));
			}

			expect(emitted).toHaveLength(703);
			expect(new Set(emitted).size).toBe(703);
			// Ordered by createdTime, then id across both projects.
			expect(emitted).toEqual(
				[...p1Tasks, ...p2Tasks].map((t) => t.id).sort(),
			);
			expect(staticData.lastCreatedTaskId).toBe("p2-699");
		});
	});

	beforeEach(() => {
		// Each test creates its own context, so nothing global to reset.
	});
});
