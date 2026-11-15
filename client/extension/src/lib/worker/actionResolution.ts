/**
 * Location/execution bookkeeping for browser actions.
 *
 * The server resolves `search_term_to_find_this_element` to a `node_id` and
 * reports the outcome in `resolution`. This module decides whether an action is
 * executable and runs the action list so a single unresolved element does not
 * abort the whole workflow.
 *
 * Keep this file free of imports and runtime-only syntax: it is covered by
 * `node --test` with type stripping only.
 */

/** Search the model asked for, mirrored from the server's `search_scope`. */
export type SearchScope = "block_ids" | "document";

/**
 * Server-reported outcome of resolving a search term to a DOM element.
 * `reason` is one of `no_match`, `missing_node_id`, `missing_search_term` or
 * `not_indexed` when `located` is false.
 */
export interface ActionResolution {
  located: boolean;
  /** Whether the action needs a DOM element (server-authoritative). */
  requires_element?: boolean;
  search_scope?: SearchScope;
  fallback_used: boolean;
  reason?: string;
}

/** The subset of an AI action this module cares about. */
export interface ResolvableAction {
  action_type: string;
  search_term_to_find_this_element?: string | null;
  node_id?: number | null;
  resolution?: ActionResolution | null;
}

export type ActionResultStatus = "success" | "failed";

export interface ActionExecutionResult<T extends ResolvableAction = ResolvableAction> {
  index: number;
  action: T;
  status: ActionResultStatus;
  /** Machine readable failure cause, absent on success. */
  reason?: string;
  /** Which search produced the element, when the server reported one. */
  search_scope?: SearchScope;
  fallback_used?: boolean;
  /** Human readable failure detail. */
  message?: string;
}

export interface ExecutionReport<T extends ResolvableAction = ResolvableAction> {
  succeeded: ActionExecutionResult<T>[];
  failed: ActionExecutionResult<T>[];
  /** Actions the executor does not implement. Previously a silent no-op. */
  skipped: ActionExecutionResult<T>[];
}

/** Runs a single action. Returning `false` means the action type is unsupported. */
export type ActionRunner<T extends ResolvableAction> = (
  action: T,
  index: number
) => Promise<boolean | void>;

const ELEMENT_ACTION_TYPES = new Set(["click", "input", "select", "hover", "focus"]);

export const requiresElement = (actionType: string): boolean =>
  ELEMENT_ACTION_TYPES.has(actionType);

/**
 * Returns why an action must not be sent to the debugger, or `null` when it
 * should be executed.
 *
 * An element action is skipped when the server marked it as unlocated, or -
 * defensively, for older server responses without a `resolution` - when it has
 * no `node_id` to look up. Actions such as `goto_url` or `enter` never need an
 * element; they are executed unless the server explicitly reported them as
 * unlocated.
 */
export function elementSkipReason(action: ResolvableAction): string | null {
  const resolution = action.resolution;

  if (!requiresElement(action.action_type)) {
    // The server may still report the action as unlocated if it decided the
    // action needs an element; trust its verdict in that case.
    return resolution && !resolution.located ? resolution.reason || "unlocated" : null;
  }

  if (resolution && !resolution.located) {
    return resolution.reason || "unlocated";
  }

  if (action.node_id === null || action.node_id === undefined) {
    return resolution?.reason || "missing_node_id";
  }

  return null;
}

const describeAction = (action: ResolvableAction): string => {
  const term = action.search_term_to_find_this_element;
  return term ? `${action.action_type} "${term}"` : action.action_type;
};

const failedResult = <T extends ResolvableAction>(
  action: T,
  index: number,
  reason: string,
  message: string
): ActionExecutionResult<T> => {
  const resolution = action.resolution;
  const result: ActionExecutionResult<T> = {
    index,
    action,
    status: "failed",
    reason,
    message,
  };

  if (resolution?.search_scope) {
    result.search_scope = resolution.search_scope;
    result.fallback_used = resolution.fallback_used;
  }

  return result;
};

/**
 * Executes every action in order.
 *
 * Unlocated element actions are never handed to the runner, so the extension
 * does not call the debugger for them. They are recorded as failures and the
 * remaining actions still run. Runner errors on located actions are also
 * recorded per action, so the caller always receives a complete report of which
 * steps worked. Actions the executor does not implement (the runner returns
 * `false`) go to `skipped` and keep their previous no-op behaviour.
 */
export async function runActions<T extends ResolvableAction>(
  actions: T[],
  runner: ActionRunner<T>
): Promise<ExecutionReport<T>> {
  const report: ExecutionReport<T> = { succeeded: [], failed: [], skipped: [] };

  for (let index = 0; index < actions.length; index++) {
    const action = actions[index];
    const skipReason = elementSkipReason(action);

    if (skipReason) {
      report.failed.push(
        failedResult(
          action,
          index,
          skipReason,
          `Skipped ${describeAction(
            action
          )}: element was not located (${skipReason})`
        )
      );
      continue;
    }

    try {
      const handled = await runner(action, index);
      if (handled === false) {
        report.skipped.push({
          index,
          action,
          status: "failed",
          reason: "unsupported_action_type",
          message: `Unsupported action type: ${action.action_type}`,
        });
        continue;
      }

      report.succeeded.push({
        index,
        action,
        status: "success",
        search_scope: action.resolution?.search_scope,
        fallback_used: action.resolution?.fallback_used,
      });
    } catch (error) {
      report.failed.push(
        failedResult(
          action,
          index,
          "execution_error",
          `Failed to execute ${describeAction(action)}: ${
            error instanceof Error ? error.message : String(error)
          }`
        )
      );
    }
  }

  return report;
}

/** Short human readable summary of failures, or `null` when everything worked. */
export function summarizeFailures(
  report: ExecutionReport<ResolvableAction>
): string | null {
  if (report.failed.length === 0) {
    return null;
  }

  return report.failed
    .map((failure) => failure.message || `${failure.action.action_type} failed`)
    .join("\n");
}
