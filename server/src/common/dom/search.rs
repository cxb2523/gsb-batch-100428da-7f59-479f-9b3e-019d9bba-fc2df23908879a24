use anyhow::Result;
use meilisearch_sdk::{client::Client as MeilisearchClient, Index, SearchResults};

use crate::common::{
    dom::parse::{parse_html_extract_imp_elements, ImportantElement},
    types::{
        AIAction, AIActions, ActionResolution, BrowserAction, LocateFailureReason, SearchScope,
    },
};

pub async fn build_index(
    html: &str,
    user_actions: &Option<Vec<BrowserAction>>,
    meilisearch_client: &MeilisearchClient,
) -> Result<(String, Index)> {
    let (document_id, important_elements) = parse_html_extract_imp_elements(html, user_actions)?;
    let pages = meilisearch_client.index("pages_new");
    pages
        .set_filterable_attributes(&["id", "document_id", "block_ids"])
        .await?;
    pages.set_ranking_rules(&["exactness"]).await?;

    let index_elements = pages.add_documents(&important_elements, Some("id")).await?;

    index_elements
        .wait_for_completion(meilisearch_client, None, None)
        .await?;

    Ok((document_id, pages))
}

async fn execute_search(
    index: &Index,
    search_term: &str,
    filter_string: &str,
    limit: usize,
) -> Result<SearchResults<ImportantElement>, meilisearch_sdk::Error> {
    index
        .search()
        .with_query(search_term)
        .with_show_ranking_score(true)
        .with_limit(limit)
        .with_filter(filter_string)
        .execute::<ImportantElement>()
        .await
}

/// Result of resolving a search term against the DOM index, together with the
/// search that actually produced it.
#[derive(Debug)]
pub struct SearchOutcome {
    pub hits: Vec<ImportantElement>,
    pub scope: SearchScope,
    /// `true` when the block limited search returned nothing and the whole
    /// document had to be searched instead.
    pub fallback_used: bool,
}

/// Decides which hits to use once both searches have run.
///
/// `block_limited` is `None` when the model did not supply `block_ids` (so the
/// block search never ran), `Some([])` when the block limited search had no
/// hits, and `Some(hits)` otherwise. This function is pure so the fallback rules
/// can be covered by tests without a running Meilisearch instance.
pub fn select_search_outcome(
    block_limited: Option<Vec<ImportantElement>>,
    document_wide: Vec<ImportantElement>,
) -> SearchOutcome {
    match block_limited {
        Some(block_hits) if !block_hits.is_empty() => SearchOutcome {
            hits: block_hits,
            scope: SearchScope::BlockIds,
            fallback_used: false,
        },
        Some(_) => SearchOutcome {
            hits: document_wide,
            scope: SearchScope::Document,
            fallback_used: true,
        },
        None => SearchOutcome {
            hits: document_wide,
            scope: SearchScope::Document,
            fallback_used: false,
        },
    }
}

fn hits_into_elements(search_results: SearchResults<ImportantElement>) -> Vec<ImportantElement> {
    search_results
        .hits
        .into_iter()
        .map(|hit| hit.result)
        .collect()
}

/// Searches the index for `search_term`.
///
/// When `block_ids` is provided the search is first limited to those blocks;
/// if that returns nothing we fall back to a document wide search. The returned
/// [`SearchOutcome`] records which of the two searches was used.
pub async fn search_with_scope(
    index: &Index,
    document_id: &str,
    search_term: &str,
    block_ids: &Option<String>,
    limit: usize,
) -> Result<SearchOutcome> {
    let document_filter = format!("document_id = \"{}\"", document_id);

    match block_ids {
        Some(ids) => {
            let block_filter = format!("{} AND block_ids = \"{}\"", document_filter, ids);
            let block_results = execute_search(index, search_term, &block_filter, limit).await?;

            if !block_results.hits.is_empty() {
                return Ok(select_search_outcome(
                    Some(hits_into_elements(block_results)),
                    Vec::new(),
                ));
            }

            // Fallback: the search term exists somewhere else on the page.
            let document_results =
                execute_search(index, search_term, &document_filter, limit).await?;

            Ok(select_search_outcome(
                Some(Vec::new()),
                hits_into_elements(document_results),
            ))
        }
        None => {
            let document_results =
                execute_search(index, search_term, &document_filter, limit).await?;

            Ok(select_search_outcome(
                None,
                hits_into_elements(document_results),
            ))
        }
    }
}

/// Attaches the outcome of a search to the action, marking it as unlocated when
/// nothing usable was found instead of leaving a dangling empty `node_id`.
///
/// Only the best hit is considered, matching the existing "take the first
/// result" behaviour: if that element has no node id there is no point in
/// guessing a lower ranked one.
pub fn apply_search_outcome(action: &mut AIAction, outcome: &SearchOutcome) {
    match outcome.hits.first() {
        Some(hit) if hit.node_id.is_some() => {
            action.node_id = hit.node_id;
            action.block_ids = hit.block_ids.clone();
            action.resolution = Some(ActionResolution::located(
                outcome.scope,
                outcome.fallback_used,
            ));
        }
        Some(_) => {
            // The search matched, but the element carries no node id, so there
            // is nothing the browser could click on.
            action.node_id = None;
            action.resolution = Some(ActionResolution::unlocated(
                Some(outcome.scope),
                outcome.fallback_used,
                LocateFailureReason::MissingNodeId,
            ));
        }
        None => {
            action.node_id = None;
            action.resolution = Some(ActionResolution::unlocated(
                Some(outcome.scope),
                outcome.fallback_used,
                LocateFailureReason::NoMatch,
            ));
        }
    }
}

fn search_term_of(action: &AIAction) -> Option<String> {
    action
        .search_term_to_find_this_element
        .as_deref()
        .map(str::trim)
        .filter(|term| !term.is_empty())
        .map(str::to_string)
}

/// Marks every action that cannot be executed against a concrete element.
///
/// Used when no HTML was supplied and therefore no index was built: the client
/// must not try to look those elements up in the browser.
pub fn mark_actions_without_index(ai_actions: &mut AIActions) {
    for ai_action in &mut ai_actions.actions {
        if ai_action.requires_element() {
            ai_action.node_id = None;
            ai_action.resolution = Some(ActionResolution::unlocated(
                None,
                false,
                LocateFailureReason::NotIndexed,
            ));
        } else {
            ai_action.resolution = Some(ActionResolution::not_required());
        }
    }
}

pub async fn build_index_and_attach_node_id(
    html: &str,
    user_actions: &Option<Vec<BrowserAction>>,
    ai_actions: &mut AIActions,
    meilisearch_client: &MeilisearchClient,
) -> Result<()> {
    let (document_id, index) = build_index(html, user_actions, meilisearch_client).await?;

    for ai_action in &mut ai_actions.actions {
        if !ai_action.requires_element() {
            ai_action.resolution = Some(ActionResolution::not_required());
            continue;
        }

        let Some(search_term) = search_term_of(ai_action) else {
            ai_action.node_id = None;
            ai_action.resolution = Some(ActionResolution::unlocated(
                None,
                false,
                LocateFailureReason::MissingSearchTerm,
            ));
            continue;
        };

        let outcome =
            search_with_scope(&index, &document_id, &search_term, &ai_action.block_ids, 5).await?;

        apply_search_outcome(ai_action, &outcome);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::types::ActionType;

    fn element(node_id: Option<i32>, block_ids: Option<&str>) -> ImportantElement {
        ImportantElement {
            inner_text: "Submit".to_string(),
            placeholder: None,
            node_id,
            tag: "button".to_string(),
            id: "element-id".to_string(),
            document_id: "document-id".to_string(),
            block_ids: block_ids.map(str::to_string),
        }
    }

    fn click_action(block_ids: Option<&str>) -> AIAction {
        AIAction {
            action_type: ActionType::Click,
            search_term_to_find_this_element: Some("Submit".to_string()),
            value: None,
            node_id: None,
            thought_process: None,
            block_ids: block_ids.map(str::to_string),
            resolution: None,
        }
    }

    #[test]
    fn block_limited_hit_is_used_without_falling_back() {
        let outcome = select_search_outcome(
            Some(vec![element(Some(11), Some("A1"))]),
            vec![element(Some(99), Some("D4"))],
        );

        assert_eq!(outcome.scope, SearchScope::BlockIds);
        assert!(!outcome.fallback_used);
        assert_eq!(outcome.hits.len(), 1);
        assert_eq!(outcome.hits[0].node_id, Some(11));
    }

    #[test]
    fn search_term_only_present_in_the_document_falls_back() {
        // The block limited search matched nothing...
        let outcome = select_search_outcome(Some(Vec::new()), vec![element(Some(42), Some("C2"))]);

        assert_eq!(outcome.scope, SearchScope::Document);
        assert!(outcome.fallback_used);
        assert_eq!(outcome.hits.len(), 1);
        assert_eq!(outcome.hits[0].node_id, Some(42));
    }

    #[test]
    fn fallback_hit_is_attached_and_reports_the_document_search() {
        let outcome = select_search_outcome(Some(Vec::new()), vec![element(Some(42), None)]);
        let mut action = click_action(Some("A1"));

        apply_search_outcome(&mut action, &outcome);

        assert_eq!(action.node_id, Some(42));
        let resolution = action.resolution.expect("resolution must be recorded");
        assert!(resolution.located);
        assert_eq!(resolution.search_scope, Some(SearchScope::Document));
        assert!(resolution.fallback_used);
        assert_eq!(resolution.reason, None);
    }

    #[test]
    fn no_hit_at_all_marks_the_action_unlocated() {
        let outcome = select_search_outcome(Some(Vec::new()), Vec::new());
        let mut action = click_action(Some("A1"));

        apply_search_outcome(&mut action, &outcome);

        assert_eq!(action.node_id, None);
        let resolution = action.resolution.expect("resolution must be recorded");
        assert!(!resolution.located);
        assert_eq!(resolution.reason, Some(LocateFailureReason::NoMatch));
        // Both searches ran, the fallback one produced nothing either.
        assert_eq!(resolution.search_scope, Some(SearchScope::Document));
        assert!(resolution.fallback_used);
    }

    #[test]
    fn hit_without_node_id_marks_the_action_unlocated() {
        let outcome = select_search_outcome(None, vec![element(None, Some("B1"))]);
        let mut action = click_action(None);

        apply_search_outcome(&mut action, &outcome);

        assert_eq!(action.node_id, None);
        let resolution = action.resolution.expect("resolution must be recorded");
        assert!(!resolution.located);
        assert_eq!(resolution.reason, Some(LocateFailureReason::MissingNodeId));
        assert_eq!(resolution.search_scope, Some(SearchScope::Document));
        assert!(!resolution.fallback_used);
    }

    #[test]
    fn first_hit_without_node_id_wins_over_lower_ranked_hits() {
        // Meilisearch returns the best match first; a lower ranked element with
        // a node id is not a safe substitute.
        let outcome = select_search_outcome(
            None,
            vec![element(None, None), element(Some(7), Some("A3"))],
        );
        let mut action = click_action(None);

        apply_search_outcome(&mut action, &outcome);

        assert_eq!(action.node_id, None);
        let resolution = action.resolution.expect("resolution must be recorded");
        assert!(!resolution.located);
        assert_eq!(resolution.reason, Some(LocateFailureReason::MissingNodeId));
    }

    #[test]
    fn document_search_without_block_ids_reports_no_fallback() {
        let outcome = select_search_outcome(None, vec![element(Some(5), Some("B2"))]);

        assert_eq!(outcome.scope, SearchScope::Document);
        assert!(!outcome.fallback_used);
    }

    #[test]
    fn actions_that_do_not_need_an_element_are_not_searched() {
        let mut ai_actions = AIActions {
            actions: vec![
                AIAction {
                    action_type: ActionType::GotoUrl,
                    search_term_to_find_this_element: None,
                    value: Some("https://example.com".to_string()),
                    node_id: None,
                    thought_process: None,
                    block_ids: None,
                    resolution: None,
                },
                click_action(Some("A1")),
            ],
        };

        mark_actions_without_index(&mut ai_actions);

        assert!(ai_actions.actions[0].resolution.as_ref().unwrap().located);
        assert_eq!(
            ai_actions.actions[1].resolution.as_ref().unwrap().reason,
            Some(LocateFailureReason::NotIndexed)
        );
        assert!(!ai_actions.actions[1].resolution.as_ref().unwrap().located);
    }

    #[test]
    fn blank_search_terms_are_not_searched() {
        let mut action = click_action(None);
        action.search_term_to_find_this_element = Some("   ".to_string());
        assert_eq!(search_term_of(&action), None);

        action.search_term_to_find_this_element = Some("  Submit  ".to_string());
        assert_eq!(search_term_of(&action), Some("Submit".to_string()));

        action.search_term_to_find_this_element = None;
        assert_eq!(search_term_of(&action), None);
    }

    #[test]
    fn resolution_is_serialized_for_the_extension() {
        let located = ActionResolution::located(SearchScope::Document, true);
        let value = serde_json::to_value(&located).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "located": true,
                "requires_element": true,
                "search_scope": "document",
                "fallback_used": true
            })
        );

        let unlocated = ActionResolution::unlocated(
            Some(SearchScope::BlockIds),
            false,
            LocateFailureReason::MissingNodeId,
        );
        let value = serde_json::to_value(&unlocated).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "located": false,
                "requires_element": true,
                "search_scope": "block_ids",
                "fallback_used": false,
                "reason": "missing_node_id"
            })
        );

        let not_required = ActionResolution::not_required();
        let value = serde_json::to_value(&not_required).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "located": true,
                "requires_element": false,
                "fallback_used": false
            })
        );
    }

    #[test]
    fn gpt_response_without_resolution_still_parses() {
        // The model never emits `resolution`; the field is filled in after the
        // index search, so an absent option must remain valid.
        let raw = r#"{
            "actions": [
                {
                    "action_type": "click",
                    "thought_process": "open menu",
                    "search_term_to_find_this_element": "Submit"
                },
                {
                    "action_type": "goto_url",
                    "value": "https://example.com"
                }
            ]
        }"#;

        let parsed: AIActions = serde_json::from_str(raw).unwrap();

        assert_eq!(parsed.actions.len(), 2);
        assert!(parsed.actions[0].resolution.is_none());
        assert!(parsed.actions[0].requires_element());
        assert!(parsed.actions[1].resolution.is_none());
        assert!(!parsed.actions[1].requires_element());
    }

    #[test]
    fn resolution_is_deserialized_back_from_the_wire_format() {
        let wire = serde_json::json!({
            "located": false,
            "search_scope": "document",
            "fallback_used": true,
            "reason": "no_match"
        });
        let resolution: ActionResolution = serde_json::from_value(wire).unwrap();

        assert!(!resolution.located);
        assert_eq!(resolution.search_scope, Some(SearchScope::Document));
        assert!(resolution.fallback_used);
        assert_eq!(resolution.reason, Some(LocateFailureReason::NoMatch));
        // Older payloads without `requires_element` must still deserialize.
        assert!(!resolution.requires_element);
    }
}
