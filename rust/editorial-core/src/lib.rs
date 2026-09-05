use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use thiserror::Error;

pub const CONTRACT_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
pub struct TraversalProof {
    pub pages: u64,
    pub raw_count: u64,
    pub valid_response: bool,
    pub exhausted: bool,
    pub resumed: bool,
    pub lower_boundary: bool,
    pub failed: bool,
}

pub fn evaluate_completeness(proof: &TraversalProof) -> CompletenessState {
    if !proof.failed
        && proof.pages > 0
        && proof.valid_response
        && proof.exhausted
        && !proof.resumed
        && !proof.lower_boundary
    {
        CompletenessState::Complete
    } else if proof.raw_count > 0 {
        CompletenessState::Partial
    } else {
        CompletenessState::Unproven
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CompletenessState {
    Attempting,
    Complete,
    Partial,
    Unproven,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum QueueState {
    Pending,
    Reviewing,
    Deferred,
    Ready,
    Skipped,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceWindowState {
    pub source_handle: String,
    pub window_start: String,
    pub window_end: String,
    pub completeness: CompletenessState,
    #[serde(default)]
    pub complete_through: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueueItem {
    pub source_handle: String,
    pub external_post_id: String,
    pub source_order: u32,
    pub post_order: u32,
    pub state: QueueState,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CoreError {
    #[error("invalid RFC3339 timestamp")]
    InvalidTimestamp,
    #[error("cursor must be within the proven source window")]
    CursorOutsideWindow,
    #[error("only COMPLETE source windows may advance a cursor")]
    CursorAdvanceWithoutProof,
    #[error("cursor regression from {current} to {candidate}")]
    CursorRegression { current: String, candidate: String },
    #[error("duplicate idempotency key: {0}")]
    DuplicateIdempotencyKey(String),
    #[error("invalid queue transition from {from:?} to {to:?}")]
    InvalidQueueTransition { from: QueueState, to: QueueState },
}

pub fn advance_complete_through(
    state: &SourceWindowState,
    candidate: &str,
) -> Result<String, CoreError> {
    if state.completeness != CompletenessState::Complete {
        return Err(CoreError::CursorAdvanceWithoutProof);
    }
    let parse = |value: &str| {
        chrono::DateTime::parse_from_rfc3339(value).map_err(|_| CoreError::InvalidTimestamp)
    };
    let candidate_time = parse(candidate)?;
    let start = parse(&state.window_start)?;
    let end = parse(&state.window_end)?;
    if start >= end || candidate_time < start || candidate_time > end {
        return Err(CoreError::CursorOutsideWindow);
    }
    if let Some(current) = state.complete_through.as_deref() {
        if candidate_time < parse(current)? {
            return Err(CoreError::CursorRegression {
                current: current.to_owned(),
                candidate: candidate.to_owned(),
            });
        }
    }
    Ok(candidate.to_owned())
}

pub fn idempotency_key(source_handle: &str, external_post_id: &str) -> String {
    format!(
        "{}:{}",
        source_handle.trim().trim_start_matches('@').to_lowercase(),
        external_post_id.trim()
    )
}

pub fn assert_unique_items(items: &[QueueItem]) -> Result<(), CoreError> {
    let mut seen = BTreeSet::new();
    for item in items {
        let key = idempotency_key(&item.source_handle, &item.external_post_id);
        if !seen.insert(key.clone()) {
            return Err(CoreError::DuplicateIdempotencyKey(key));
        }
    }
    Ok(())
}

pub fn source_first_order(items: &mut [QueueItem]) {
    items.sort_by(|a, b| {
        (
            a.source_order,
            idempotency_key(&a.source_handle, ""),
            a.post_order,
            &a.source_handle,
            &a.external_post_id,
        )
            .cmp(&(
                b.source_order,
                idempotency_key(&b.source_handle, ""),
                b.post_order,
                &b.source_handle,
                &b.external_post_id,
            ))
    });
}

pub fn transition_queue(item: &mut QueueItem, to: QueueState) -> Result<(), CoreError> {
    use QueueState::*;
    let allowed = matches!(
        (item.state, to),
        (Pending, Reviewing)
            | (Pending, Deferred)
            | (Pending, Skipped)
            | (Reviewing, Ready)
            | (Reviewing, Deferred)
            | (Reviewing, Skipped)
            | (Deferred, Pending)
            | (Deferred, Reviewing)
    ) || item.state == to;
    if !allowed {
        return Err(CoreError::InvalidQueueTransition {
            from: item.state,
            to,
        });
    }
    item.state = to;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn completeness_requires_raw_terminal_proof() {
        for bits in 0..64 {
            let proof = TraversalProof {
                pages: 1,
                raw_count: 0,
                valid_response: bits & 1 != 0,
                exhausted: bits & 2 != 0,
                resumed: bits & 4 != 0,
                lower_boundary: bits & 8 != 0,
                failed: bits & 16 != 0,
            };
            let expected = if bits & 31 == 3 {
                CompletenessState::Complete
            } else {
                CompletenessState::Unproven
            };
            assert_eq!(evaluate_completeness(&proof), expected);
        }
        let mut proof = TraversalProof {
            pages: 0,
            raw_count: 0,
            valid_response: true,
            exhausted: true,
            resumed: false,
            lower_boundary: false,
            failed: false,
        };
        assert_eq!(evaluate_completeness(&proof), CompletenessState::Unproven);
        proof.raw_count = 3;
        assert_eq!(evaluate_completeness(&proof), CompletenessState::Partial);
    }

    fn window(state: CompletenessState, through: Option<&str>) -> SourceWindowState {
        SourceWindowState {
            source_handle: "alpha".into(),
            window_start: "2026-09-05T00:00:00Z".into(),
            window_end: "2026-09-05T01:00:00Z".into(),
            completeness: state,
            complete_through: through.map(str::to_owned),
        }
    }

    fn item(source: &str, id: &str, source_order: u32, post_order: u32) -> QueueItem {
        QueueItem {
            source_handle: source.into(),
            external_post_id: id.into(),
            source_order,
            post_order,
            state: QueueState::Pending,
        }
    }

    #[test]
    fn only_complete_can_advance_cursor() {
        for state in [
            CompletenessState::Attempting,
            CompletenessState::Partial,
            CompletenessState::Unproven,
        ] {
            assert_eq!(
                advance_complete_through(&window(state, None), "2026-09-05T00:30:00Z"),
                Err(CoreError::CursorAdvanceWithoutProof)
            );
        }
        assert_eq!(
            advance_complete_through(
                &window(CompletenessState::Complete, None),
                "2026-09-05T00:30:00Z"
            )
            .unwrap(),
            "2026-09-05T00:30:00Z"
        );
    }

    #[test]
    fn complete_cursor_is_monotonic() {
        assert!(matches!(
            advance_complete_through(
                &window(CompletenessState::Complete, Some("2026-09-05T00:30:00Z")),
                "2026-09-05T00:00:00Z"
            ),
            Err(CoreError::CursorRegression { .. })
        ));
        assert_eq!(
            advance_complete_through(
                &window(CompletenessState::Complete, Some("2026-09-05T00:30:00Z")),
                "2026-09-05T00:30:00Z"
            )
            .unwrap(),
            "2026-09-05T00:30:00Z"
        );
        assert_eq!(
            advance_complete_through(
                &window(CompletenessState::Complete, Some("2026-09-05T00:30:00Z")),
                "2026-09-05T01:00:00Z"
            )
            .unwrap(),
            "2026-09-05T01:00:00Z"
        );
    }

    #[test]
    fn timestamps_compare_instants_and_reject_unproven_bounds() {
        let state = window(
            CompletenessState::Complete,
            Some("2026-09-05T01:30:00+01:00"),
        );
        assert!(matches!(
            advance_complete_through(&state, "2026-09-05T00:15:00Z"),
            Err(CoreError::CursorRegression { .. })
        ));
        assert!(advance_complete_through(&state, "2026-09-05T00:45:00Z").is_ok());
        assert_eq!(
            advance_complete_through(&state, "invalid"),
            Err(CoreError::InvalidTimestamp)
        );
        assert_eq!(
            advance_complete_through(&state, "2026-09-05T02:00:00Z"),
            Err(CoreError::CursorOutsideWindow)
        );
    }

    #[test]
    fn idempotency_key_normalizes_source() {
        assert_eq!(idempotency_key(" @HannieZone ", " 123 "), "hanniezone:123");
    }

    #[test]
    fn duplicate_items_are_rejected() {
        let items = vec![item("Alpha", "1", 0, 0), item("@alpha", "1", 0, 1)];
        assert!(matches!(
            assert_unique_items(&items),
            Err(CoreError::DuplicateIdempotencyKey(_))
        ));
    }

    #[test]
    fn ordering_is_source_first_and_deterministic() {
        let mut items = vec![
            item("beta", "2", 1, 0),
            item("alpha", "2", 0, 1),
            item("alpha", "1", 0, 0),
        ];
        source_first_order(&mut items);
        let ids: Vec<_> = items
            .iter()
            .map(|x| (x.source_handle.as_str(), x.external_post_id.as_str()))
            .collect();
        assert_eq!(ids, vec![("alpha", "1"), ("alpha", "2"), ("beta", "2")]);
    }

    #[test]
    fn invalid_queue_transition_is_rejected() {
        let mut x = item("alpha", "1", 0, 0);
        assert!(matches!(
            transition_queue(&mut x, QueueState::Ready),
            Err(CoreError::InvalidQueueTransition { .. })
        ));
        transition_queue(&mut x, QueueState::Reviewing).unwrap();
        transition_queue(&mut x, QueueState::Ready).unwrap();
    }

    #[test]
    fn tied_source_ranks_still_keep_each_source_together() {
        let mut items = vec![
            item("beta", "1", 0, 0),
            item("alpha", "2", 0, 1),
            item("@Alpha", "1", 0, 0),
        ];
        source_first_order(&mut items);
        assert_eq!(
            items
                .iter()
                .map(|x| x.external_post_id.as_str())
                .collect::<Vec<_>>(),
            vec!["1", "2", "1"]
        );
        assert_eq!(items[2].source_handle, "beta");
    }

    #[test]
    fn contract_serializes_with_stable_names() {
        let encoded = serde_json::to_string(&window(CompletenessState::Partial, None)).unwrap();
        assert!(encoded.contains("\"partial\""));
        assert_eq!(CONTRACT_VERSION, 1);
    }
}
