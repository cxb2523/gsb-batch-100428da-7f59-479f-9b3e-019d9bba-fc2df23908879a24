import type {
	IDataObject,
	ILoadOptionsFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	IPollFunctions,
} from "n8n-workflow";
import { NodeConnectionTypes, NodeOperationError } from "n8n-workflow";

import {
	getAuthenticationType,
	tickTickApiRequest,
	tickTickApiRequestV2,
} from "./helpers/apiRequest";
import { ENDPOINTS } from "./helpers/constants";
import { testTickTickSessionApi } from "./helpers/credentialTest";
import {
	getProjects,
	searchAssigneesForSelectedProjects,
	searchProjects as searchProjectsHelper,
} from "./helpers/loadOptions";
import type { Task, TaskAssignee } from "./helpers/types";

interface PollStaticData extends IDataObject {
	/**
	 * Timestamp of the last emitted task. Kept as its own key for backwards
	 * compatibility with workflows whose static data predates the composite
	 * watermark.
	 */
	lastCreatedTime?: string;
	/** Id of the last emitted task, used to disambiguate equal timestamps. */
	lastCreatedTaskId?: string;
}

/**
 * Watermark identifying the last emitted task.
 *
 * `createdTime` alone is not enough: several tasks can share the same
 * millisecond, and a strict `>` comparison on the timestamp would drop all but
 * one of them forever. The task id breaks that tie deterministically.
 */
interface TaskWatermark {
	createdTime: string;
	/**
	 * Id of the last emitted task.
	 *
	 * - a string (possibly empty when the task carried no id) means composite
	 *   watermark, so only same-millisecond tasks sorting after it are pending;
	 * - `undefined` means the state predates composite watermarks and only
	 *   carries a timestamp.
	 */
	taskId: string | undefined;
}

interface ProjectListItem {
	id: string;
	name?: string;
	closed?: boolean;
}

interface ProjectDataResponse {
	tasks?: Task[];
}

function extractFixedCollectionIds(
	raw: unknown,
	itemKey: string,
): string[] {
	const items =
		(raw as { [k: string]: Array<{ value?: unknown }> } | undefined)?.[
			itemKey
		] ?? [];
	return items
		.map((row) => {
			const v = row?.value;
			if (v && typeof v === "object") {
				return String((v as { value?: unknown }).value ?? "");
			}
			return v !== undefined && v !== null ? String(v) : "";
		})
		.filter((id) => id.length > 0);
}

function extractAssigneeId(assignee: Task["assignee"]): string | undefined {
	if (assignee === undefined || assignee === null) return undefined;
	if (typeof assignee === "object") {
		const id = (assignee as TaskAssignee).userId;
		return id !== undefined && id !== null ? String(id) : undefined;
	}
	return String(assignee);
}

const MAX_EMITTED_PER_POLL = 500;

function createdTimeToMs(createdTime: string): number {
	return Date.parse(createdTime);
}

function compareCreatedTime(a: string, b: string): number {
	const aMs = createdTimeToMs(a);
	const bMs = createdTimeToMs(b);
	if (!Number.isNaN(aMs) && !Number.isNaN(bMs)) return aMs - bMs;
	return a.localeCompare(b);
}

function taskIdOf(task: Task): string {
	return task.id === undefined || task.id === null ? "" : String(task.id);
}

/**
 * Ordering used both for sorting the fetched tasks and for advancing the
 * watermark, so that a task can never sort before the watermark and still be
 * considered newer than it.
 */
function compareTasks(a: Task, b: Task): number {
	const byTime = compareCreatedTime(
		a.createdTime as string,
		b.createdTime as string,
	);
	if (byTime !== 0) return byTime;
	return taskIdOf(a).localeCompare(taskIdOf(b));
}

function readWatermark(staticData: PollStaticData): TaskWatermark | undefined {
	const createdTime = staticData.lastCreatedTime;
	if (typeof createdTime !== "string" || createdTime.length === 0) {
		return undefined;
	}
	// Key presence, not emptiness, separates "no id recorded because the task
	// had none" from legacy timestamp-only state.
	const hasTaskId = Object.prototype.hasOwnProperty.call(
		staticData,
		"lastCreatedTaskId",
	);
	const rawTaskId = staticData.lastCreatedTaskId;
	return {
		createdTime,
		taskId: hasTaskId
			? typeof rawTaskId === "string"
				? rawTaskId
				: ""
			: undefined,
	};
}

function writeWatermark(staticData: PollStaticData, task: Task): void {
	staticData.lastCreatedTime = task.createdTime as string;
	staticData.lastCreatedTaskId = taskIdOf(task);
}

function isAfterWatermark(task: Task, watermark: TaskWatermark): boolean {
	const byTime = compareCreatedTime(
		task.createdTime as string,
		watermark.createdTime,
	);
	if (byTime !== 0) return byTime > 0;
	// Static data written before the composite watermark only stored a
	// timestamp. Its same-millisecond tasks may still be unemitted (the old
	// comparison dropped them), so treat that whole millisecond as pending.
	// At worst this re-emits tasks from a single millisecond once.
	if (watermark.taskId === undefined) return true;
	return taskIdOf(task).localeCompare(watermark.taskId) > 0;
}

async function fetchTasksV1(
	context: IPollFunctions,
	projectIds: string[],
): Promise<Task[]> {
	let projectsToScan = projectIds;
	if (projectsToScan.length === 0) {
		const projects = (await tickTickApiRequest.call(
			context,
			"GET",
			ENDPOINTS.OPEN_V1_PROJECT,
		)) as ProjectListItem[];
		projectsToScan = (projects ?? [])
			.filter((p) => p && p.id && !p.closed)
			.map((p) => p.id);
	}

	const tasks: Task[] = [];
	for (const pid of projectsToScan) {
		const data = (await tickTickApiRequest.call(
			context,
			"GET",
			ENDPOINTS.OPEN_V1_PROJECT_DATA(pid),
		)) as ProjectDataResponse;
		if (Array.isArray(data?.tasks)) {
			tasks.push(...data.tasks);
		}
	}
	return tasks;
}

async function fetchTasksV2(context: IPollFunctions): Promise<Task[]> {
	const response = (await tickTickApiRequestV2.call(
		context,
		"GET",
		ENDPOINTS.SYNC,
	)) as { syncTaskBean?: { update?: Task[]; add?: Task[] } };

	const bean = response?.syncTaskBean;
	const tasks: Task[] = [];
	if (Array.isArray(bean?.update)) tasks.push(...(bean!.update as Task[]));
	if (Array.isArray(bean?.add)) tasks.push(...(bean!.add as Task[]));
	return tasks;
}

export class TickTickTrigger implements INodeType {
	description: INodeTypeDescription = {
		displayName: "TickTick Trigger",
		name: "tickTickTrigger",
		icon: "file:../../icons/ticktick.svg",
		group: ["trigger"],
		version: 1,
		description:
			"Polls TickTick for newly created tasks, optionally filtered by project or assignee",
		subtitle: '={{ $parameter["event"] }}',
		defaults: {
			name: "TickTick Trigger",
		},
		credentials: [
			{
				name: "tickTickTokenApi",
				required: true,
				displayOptions: {
					show: { authentication: ["tickTickTokenApi"] },
				},
			},
			{
				name: "tickTickOAuth2Api",
				required: true,
				displayOptions: {
					show: { authentication: ["tickTickOAuth2Api"] },
				},
			},
			{
				name: "tickTickSessionApi",
				required: true,
				testedBy: "tickTickSessionApiTest",
				displayOptions: {
					show: { authentication: ["tickTickSessionApi"] },
				},
			},
		],
		polling: true,
		inputs: [],
		outputs: [NodeConnectionTypes.Main],
		properties: [
			{
				displayName: "Authentication",
				name: "authentication",
				type: "options",
				options: [
					{ name: "Access Token (V1 API)", value: "tickTickTokenApi" },
					{ name: "Email/Password (V2 API)", value: "tickTickSessionApi" },
					{ name: "OAuth2 (V1 API)", value: "tickTickOAuth2Api" },
				],
				default: "tickTickTokenApi",
			},
			{
				displayName: "Event",
				name: "event",
				type: "options",
				noDataExpression: true,
				default: "taskCreated",
				options: [
					{
						name: "Task Created",
						value: "taskCreated",
						description: "Trigger when new tasks are created",
					},
				],
			},
			{
				displayName: "Options",
				name: "options",
				type: "collection",
				default: {},
				placeholder: "Add option",
				options: [
					{
						displayName: "Projects",
						name: "projectIds",
						type: "fixedCollection",
						typeOptions: { multipleValues: true },
						default: {},
						placeholder: "Add Project",
						description:
							"Only emit tasks created in these projects. Leave empty to monitor all projects.",
						options: [
							{
								name: "project",
								displayName: "Project",
								values: [
									{
										displayName: "Project",
										name: "value",
										type: "resourceLocator",
										default: { mode: "list", value: "" },
										modes: [
											{
												displayName: "From List",
												name: "list",
												type: "list",
												typeOptions: {
													searchListMethod: "searchProjects",
													searchable: true,
													searchFilterRequired: false,
												},
											},
											{
												displayName: "By ID",
												name: "id",
												type: "string",
												placeholder: "e.g. 5f9b3a4c8d2e1f0012345678",
											},
										],
									},
								],
							},
						],
					},
					{
						displayName: "Assignees",
						name: "assigneeIds",
						type: "fixedCollection",
						typeOptions: { multipleValues: true },
						default: {},
						placeholder: "Add Assignee",
						description:
							"Only emit tasks assigned to these users. Leave empty for any assignee. The dropdown only populates for shared projects when using V2 (Email/Password) authentication and at least one project is selected above.",
						options: [
							{
								name: "assignee",
								displayName: "Assignee",
								values: [
									{
										displayName: "Assignee",
										name: "value",
										type: "resourceLocator",
										default: { mode: "list", value: "" },
										modes: [
											{
												displayName: "From List",
												name: "list",
												type: "list",
												typeOptions: {
													searchListMethod:
														"searchAssigneesForSelectedProjects",
													searchable: true,
													searchFilterRequired: false,
												},
											},
											{
												displayName: "By ID",
												name: "id",
												type: "string",
												placeholder: "e.g. 123456789",
											},
										],
									},
								],
							},
						],
					},
				],
			},
		],
	};

	methods = {
		loadOptions: {
			getProjects,
		},
		listSearch: {
			async searchProjects(
				this: ILoadOptionsFunctions,
				filter?: string,
			) {
				return { results: await searchProjectsHelper.call(this, filter) };
			},
			async searchAssigneesForSelectedProjects(
				this: ILoadOptionsFunctions,
				filter?: string,
			) {
				return {
					results: await searchAssigneesForSelectedProjects.call(this, filter),
				};
			},
		},
		credentialTest: {
			tickTickSessionApiTest: testTickTickSessionApi,
		},
	};

	async poll(
		this: IPollFunctions,
	): Promise<INodeExecutionData[][] | null> {
		const event = this.getNodeParameter("event") as string;
		if (event !== "taskCreated") {
			throw new NodeOperationError(
				this.getNode(),
				`Unsupported event: ${event}`,
			);
		}

		const options = this.getNodeParameter("options", {}) as {
			projectIds?: unknown;
			assigneeIds?: unknown;
		};
		const projectIds = extractFixedCollectionIds(options.projectIds, "project");
		const assigneeIds = extractFixedCollectionIds(
			options.assigneeIds,
			"assignee",
		);
		const isManual = this.getMode() === "manual";

		const authType = getAuthenticationType(this);
		const useV2 = authType === "tickTickSessionApi";

		let tasks = useV2
			? await fetchTasksV2(this)
			: await fetchTasksV1(this, projectIds);

		if (useV2 && projectIds.length > 0) {
			const allowed = new Set(projectIds);
			tasks = tasks.filter((t) => allowed.has(t.projectId));
		}

		if (assigneeIds.length > 0) {
			const allowed = new Set(assigneeIds);
			tasks = tasks.filter((t) => {
				const id = extractAssigneeId(t.assignee);
				return id !== undefined && allowed.has(id);
			});
		}

		tasks = tasks.filter((t) => typeof t.createdTime === "string");

		// Project/assignee filters above run before any watermark movement, so a
		// filtered-out task can never advance the watermark past tasks that are
		// still owed to the caller. Ordering is createdTime, then task id.
		tasks.sort(compareTasks);

		if (isManual) {
			return tasks.length > 0
				? [this.helpers.returnJsonArray(tasks.slice(-1))]
				: null;
		}

		const staticData = this.getWorkflowStaticData("node") as PollStaticData;
		const watermark = readWatermark(staticData);

		if (!watermark) {
			if (tasks.length > 0) {
				writeWatermark(staticData, tasks[tasks.length - 1]);
			}
			return null;
		}

		const pending = tasks.filter((t) => isAfterWatermark(t, watermark));
		if (pending.length === 0) return null;

		// The remaining backlog stays after the watermark and is picked up by
		// the following polls.
		const emitted = pending.slice(0, MAX_EMITTED_PER_POLL);
		writeWatermark(staticData, emitted[emitted.length - 1]);
		return [this.helpers.returnJsonArray(emitted)];
	}
}
