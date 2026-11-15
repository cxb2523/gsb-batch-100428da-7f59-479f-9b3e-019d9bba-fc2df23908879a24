import { test } from "node:test";
import assert from "node:assert/strict";

import {
  elementSkipReason,
  requiresElement,
  runActions,
  summarizeFailures,
  type ActionExecutionResult,
  type ResolvableAction,
} from "../src/lib/worker/actionResolution.ts";

type TestAction = ResolvableAction & { value?: string };

const clickOn = (
  term: string | null,
  overrides: Partial<TestAction> = {}
): TestAction => ({
  action_type: "click",
  search_term_to_find_this_element: term,
  node_id: 12,
  resolution: {
    located: true,
    search_scope: "block_ids",
    fallback_used: false,
  },
  ...overrides,
});

/** Records every action the debugger would have been asked to execute. */
const createSpyRunner = (executed: TestAction[]) =>
  async (action: TestAction): Promise<boolean> => {
    executed.push(action);
    return true;
  };

test("search term found only in the whole document is executed", async () => {
  // The server searched the requested blocks first, found nothing, then
  // searched the whole document and reported that fallback.
  const action = clickOn("Submit", {
    node_id: 42,
    resolution: {
      located: true,
      search_scope: "document",
      fallback_used: true,
    },
  });
  const executed: TestAction[] = [];

  const report = await runActions([action], createSpyRunner(executed));

  assert.equal(report.failed.length, 0);
  assert.equal(report.succeeded.length, 1);
  assert.equal(report.succeeded[0].search_scope, "document");
  assert.equal(report.succeeded[0].fallback_used, true);
  assert.deepEqual(executed, [action]);
});

test("an action with no match anywhere is skipped, not executed", async () => {
  const action = clickOn("Nonexistent", {
    node_id: null,
    resolution: {
      located: false,
      search_scope: "document",
      fallback_used: true,
      reason: "no_match",
    },
  });
  const executed: TestAction[] = [];

  const report = await runActions([action], createSpyRunner(executed));

  assert.deepEqual(executed, [], "the debugger must not be asked to find the element");
  assert.equal(report.succeeded.length, 0);
  assert.equal(report.failed.length, 1);
  assert.equal(report.failed[0].reason, "no_match");
  assert.equal(report.failed[0].status, "failed");
});

test("a hit whose element has no node id is skipped", async () => {
  const action = clickOn("Submit", {
    node_id: null,
    resolution: {
      located: false,
      search_scope: "block_ids",
      fallback_used: false,
      reason: "missing_node_id",
    },
  });
  const executed: TestAction[] = [];

  const report = await runActions([action], createSpyRunner(executed));

  assert.deepEqual(executed, []);
  assert.equal(report.failed.length, 1);
  assert.equal(report.failed[0].reason, "missing_node_id");
});

test("execution continues past unlocated actions and reports both lists", async () => {
  const first = clickOn("First", { node_id: 1 });
  const unlocated = clickOn("Missing", {
    node_id: null,
    resolution: {
      located: false,
      search_scope: "document",
      fallback_used: true,
      reason: "no_match",
    },
  });
  const input = {
    action_type: "input",
    search_term_to_find_this_element: "Email",
    value: "user@example.com",
    node_id: 7,
    resolution: {
      located: true,
      search_scope: "document" as const,
      fallback_used: false,
    },
  };
  const goto: TestAction = {
    action_type: "goto_url",
    value: "https://example.com",
  };

  const executed: TestAction[] = [];
  const report = await runActions(
    [first, unlocated, input, goto],
    createSpyRunner(executed)
  );

  // The unlocated action is the only one the executor never sees.
  assert.deepEqual(executed, [first, input, goto]);
  assert.deepEqual(
    report.succeeded.map((result) => result.index),
    [0, 2, 3]
  );
  assert.deepEqual(
    report.failed.map((result) => result.index),
    [1]
  );
  assert.equal(report.failed[0].reason, "no_match");
  assert.equal(report.skipped.length, 0);
});

test("a failing located action does not stop the rest of the list", async () => {
  const actions = [clickOn("Broken"), clickOn("Fine")];
  const executed: TestAction[] = [];

  const report = await runActions(actions, async (action) => {
    executed.push(action);
    if (action.search_term_to_find_this_element === "Broken") {
      throw new Error("Element not found");
    }
    return true;
  });

  assert.equal(executed.length, 2, "the second action must still run");
  assert.equal(report.succeeded.length, 1);
  assert.equal(report.failed.length, 1);
  assert.equal(report.failed[0].reason, "execution_error");
  assert.match(report.failed[0].message || "", /Element not found/);
});

test("unsupported action types are reported separately, not as location failures", async () => {
  const unsupported: TestAction = {
    action_type: "select",
    search_term_to_find_this_element: "Country",
    node_id: 3,
    resolution: {
      located: true,
      search_scope: "block_ids",
      fallback_used: false,
    },
  };

  const report = await runActions([unsupported], async () => false);

  assert.equal(report.succeeded.length, 0);
  assert.equal(report.failed.length, 0);
  assert.equal(report.skipped.length, 1);
  assert.equal(report.skipped[0].reason, "unsupported_action_type");
});

test("older responses without a resolution still fall back to node_id checks", () => {
  assert.equal(
    elementSkipReason({ action_type: "click", node_id: 5 }),
    null,
    "a node id is enough for actions from older servers"
  );
  assert.equal(
    elementSkipReason({ action_type: "click", node_id: null }),
    "missing_node_id"
  );
  assert.equal(
    elementSkipReason({ action_type: "goto_url" }),
    null,
    "actions without an element never need a node id"
  );
});

test("the server verdict wins for actions the client would not consider element based", () => {
  const action: TestAction = {
    action_type: "goto_url",
    value: "https://example.com",
    resolution: {
      located: false,
      requires_element: true,
      fallback_used: false,
      reason: "not_indexed",
    },
  };

  assert.equal(elementSkipReason(action), "not_indexed");
});

test("element actions include input but not navigation", () => {
  assert.equal(requiresElement("click"), true);
  assert.equal(requiresElement("input"), true);
  assert.equal(requiresElement("goto_url"), false);
  assert.equal(requiresElement("enter"), false);
});

test("failure summary lists every failed action", () => {
  const failed: ActionExecutionResult<TestAction>[] = [
    {
      index: 1,
      action: clickOn("One"),
      status: "failed",
      reason: "no_match",
      message: 'Skipped click "One": element was not located (no_match)',
    },
  ];

  const summary = summarizeFailures({ succeeded: [], failed, skipped: [] });
  assert.match(summary || "", /One/);
  assert.equal(
    summarizeFailures({ succeeded: [], failed: [], skipped: [] }),
    null
  );
});
