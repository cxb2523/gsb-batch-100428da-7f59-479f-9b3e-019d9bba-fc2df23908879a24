use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use std::fmt::Debug;

use crate::common::db::task::TaskInfo;

#[derive(Debug, Serialize, Deserialize)]
pub struct Response {
    pub status: ApiStatus,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct BrowserAction {
    pub action_type: String,
    pub timestamp: String,
    pub url: String,
    pub normalized_url: String,
    pub host: String,
    pub normalized_host: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scroll_percentage: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outer_html: Option<String>,
    pub tag: Option<String>,
    pub placeholder: Option<String>,
    pub inner_text: Option<String>,
    pub block_ids: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CreateWorkflowRequestPayload {
    pub title: String,
    pub objective: String,
    pub actions: Vec<BrowserAction>,
    pub openai_api_key: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct UpdateWorkflowRequestPayload {
    pub title: String,
    pub objective: String,
    pub id: String,
    pub tasks: Vec<Task>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CreateWorkflowResponsePayload {
    pub workflow_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ExecuteWorkflowRequestPayload {
    pub workflow_id: String,
    pub workflow_execution_id: Option<String>,
    pub html: Option<String>,
    pub screenshot: Option<String>,
    pub url: Option<String>,
    pub host: Option<String>,
    pub openai_api_key: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ExecuteWorkflowResponsePayload {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_execution_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task: Option<TaskInfo>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub action: Option<AIActions>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub all_tasks_completed: Option<bool>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ExecuteRequestPayload {
    pub messages: Vec<UserMessage>,
    pub html: Option<String>,
    pub screenshot: Option<String>,
    pub url: Option<String>,
    pub host: Option<String>,
    pub openai_api_key: Option<String>,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Tasks {
    pub tasks: Vec<Task>,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Task {
    pub task_number: i32,
    pub task: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct FunctionCall {
    pub arguments: String,
    pub name: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Message {
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub function_call: Option<FunctionCall>,
    pub role: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct UserMessage {
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub function_call: Option<FunctionCall>,
    pub role: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Choices {
    pub finish_reason: Option<String>,
    pub index: i32,
    pub message: Message,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Usage {
    pub completion_tokens: i32,
    pub prompt_tokens: i32,
    pub total_tokens: i32,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct OpenAIResponse {
    pub choices: Option<Vec<Choices>>,
    pub created: i32,
    pub id: String,
    pub model: String,
    pub object: String,
    pub usage: Usage,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub enum Value {
    String(String),
    JsonValue(JsonValue),
}

/// Which search was responsible for locating an element.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SearchScope {
    /// The search was limited to the block ids supplied by the model.
    BlockIds,
    /// The search covered the whole document (either because no block ids were
    /// supplied, or because the block limited search had no hits).
    Document,
}

/// Why an action could not be resolved to a concrete DOM element.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LocateFailureReason {
    /// Neither the block limited search nor the document wide search returned
    /// a hit.
    NoMatch,
    /// A hit was found but the element had no `node_id`/`aie-id`.
    MissingNodeId,
    /// The action needs an element but the model did not provide a search term.
    MissingSearchTerm,
    /// No HTML was supplied with the request, so no index could be built and
    /// no search was attempted.
    NotIndexed,
}

/// Auditable outcome of locating a DOM element for a single action.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct ActionResolution {
    /// Whether the action can be executed. Always `true` for actions such as
    /// `goto_url` that do not target an element (see `requires_element`).
    pub located: bool,
    /// Whether this action type needs a DOM element at all.
    #[serde(default)]
    pub requires_element: bool,
    /// Which search produced the element. `None` when no search was performed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub search_scope: Option<SearchScope>,
    /// Whether the initial block limited search was empty and we had to fall
    /// back to the document wide search.
    pub fallback_used: bool,
    /// Why the action is not located. `None` when located or when the action
    /// does not target an element.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<LocateFailureReason>,
}

impl ActionResolution {
    pub fn located(scope: SearchScope, fallback_used: bool) -> Self {
        Self {
            located: true,
            requires_element: true,
            search_scope: Some(scope),
            fallback_used,
            reason: None,
        }
    }

    pub fn unlocated(
        scope: Option<SearchScope>,
        fallback_used: bool,
        reason: LocateFailureReason,
    ) -> Self {
        Self {
            located: false,
            requires_element: true,
            search_scope: scope,
            fallback_used,
            reason: Some(reason),
        }
    }

    /// Actions such as `goto_url` or `enter` do not need a DOM element, so
    /// there is nothing that can fail to resolve.
    pub fn not_required() -> Self {
        Self {
            located: true,
            requires_element: false,
            search_scope: None,
            fallback_used: false,
            reason: None,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct AIAction {
    pub action_type: ActionType,
    pub search_term_to_find_this_element: Option<String>,
    pub value: Option<String>,
    pub node_id: Option<i32>,
    pub thought_process: Option<String>,
    pub block_ids: Option<String>,
    /// How the search term was resolved to `node_id`. Always present after the
    /// action went through DOM indexing, so clients can tell located and
    /// unlocated actions apart.
    pub resolution: Option<ActionResolution>,
}

impl AIAction {
    pub fn requires_element(&self) -> bool {
        self.action_type.requires_element_location()
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct AIActions {
    pub actions: Vec<AIAction>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ApiStatus {
    Ok,
    Error(String),
    Accepted,
}

#[derive(Debug, Deserialize, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionType {
    Click,
    ScrollUp,
    ScrollDown,
    Input,
    Select,
    Hover,
    Focus,
    Visit,
    Enter,
    KeyDown,
    Summarize,
    TaskComplete,
    Answer,
    Chat,
    Stuck,
    Translate,
    CodeGeneration,
    GotoUrl,
    ExtractAndStoreInfoForNextTask,
}

impl ActionType {
    /// Actions that are dispatched against a concrete DOM element and therefore
    /// need `search_term_to_find_this_element` to resolve to a `node_id`.
    pub fn requires_element_location(&self) -> bool {
        matches!(
            self,
            ActionType::Click
                | ActionType::Input
                | ActionType::Select
                | ActionType::Hover
                | ActionType::Focus
        )
    }
}
