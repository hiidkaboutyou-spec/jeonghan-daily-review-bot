use jeonghan_editorial_core::{
    advance_complete_through, assert_unique_items, evaluate_completeness, source_first_order,
    transition_queue, QueueItem, QueueState, SourceWindowState, TraversalProof, CONTRACT_VERSION,
};
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead};

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Request {
    EvaluateCompleteness {
        proof: TraversalProof,
    },
    AdvanceCursor {
        state: SourceWindowState,
        candidate: String,
    },
    OrderQueue {
        items: Vec<QueueItem>,
    },
    TransitionQueue {
        item: QueueItem,
        to: QueueState,
    },
}

#[derive(Debug, Deserialize)]
struct Envelope {
    contract_version: u32,
    #[serde(flatten)]
    request: Request,
}

fn parse_request(line: &str) -> Result<Request, String> {
    let envelope: Envelope =
        serde_json::from_str(line).map_err(|err| format!("invalid_request: {err}"))?;
    if envelope.contract_version != CONTRACT_VERSION {
        return Err("unsupported_contract_version".into());
    }
    Ok(envelope.request)
}

#[derive(Debug, Serialize)]
struct Response<T: Serialize> {
    contract_version: u32,
    ok: bool,
    result: Option<T>,
    error: Option<String>,
}

fn main() {
    for line in io::stdin().lock().lines() {
        let line = match line {
            Ok(line) if !line.trim().is_empty() => line,
            Ok(_) => continue,
            Err(err) => {
                emit(Response::<serde_json::Value> {
                    contract_version: CONTRACT_VERSION,
                    ok: false,
                    result: None,
                    error: Some(err.to_string()),
                });
                continue;
            }
        };
        let request = match parse_request(&line) {
            Ok(request) => request,
            Err(err) => {
                emit(Response::<serde_json::Value> {
                    contract_version: CONTRACT_VERSION,
                    ok: false,
                    result: None,
                    error: Some(err),
                });
                continue;
            }
        };
        match request {
            Request::EvaluateCompleteness { proof } => emit(Response {
                contract_version: CONTRACT_VERSION,
                ok: true,
                result: Some(evaluate_completeness(&proof)),
                error: None,
            }),
            Request::AdvanceCursor { state, candidate } => {
                match advance_complete_through(&state, &candidate) {
                    Ok(value) => emit(Response {
                        contract_version: CONTRACT_VERSION,
                        ok: true,
                        result: Some(value),
                        error: None,
                    }),
                    Err(err) => emit(Response::<String> {
                        contract_version: CONTRACT_VERSION,
                        ok: false,
                        result: None,
                        error: Some(err.to_string()),
                    }),
                }
            }
            Request::OrderQueue { mut items } => {
                if let Err(err) = assert_unique_items(&items) {
                    emit(Response::<Vec<QueueItem>> {
                        contract_version: CONTRACT_VERSION,
                        ok: false,
                        result: None,
                        error: Some(err.to_string()),
                    });
                    continue;
                }
                source_first_order(&mut items);
                emit(Response {
                    contract_version: CONTRACT_VERSION,
                    ok: true,
                    result: Some(items),
                    error: None,
                });
            }
            Request::TransitionQueue { mut item, to } => match transition_queue(&mut item, to) {
                Ok(()) => emit(Response {
                    contract_version: CONTRACT_VERSION,
                    ok: true,
                    result: Some(item),
                    error: None,
                }),
                Err(err) => emit(Response::<QueueItem> {
                    contract_version: CONTRACT_VERSION,
                    ok: false,
                    result: None,
                    error: Some(err.to_string()),
                }),
            },
        }
    }
}

fn emit<T: Serialize>(response: Response<T>) {
    println!(
        "{}",
        serde_json::to_string(&response).expect("response must serialize")
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_version_is_required_and_checked() {
        assert!(parse_request(r#"{"contract_version":1,"op":"order_queue","items":[]}"#).is_ok());
        assert!(parse_request(r#"{"contract_version":2,"op":"order_queue","items":[]}"#).is_err());
        assert!(parse_request(r#"{"op":"order_queue","items":[]}"#).is_err());
    }
}
