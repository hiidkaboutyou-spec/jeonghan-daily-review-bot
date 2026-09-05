use jeonghan_editorial_core::{
    advance_complete_through, assert_unique_items, source_first_order, transition_queue, QueueItem,
    QueueState, SourceWindowState, CONTRACT_VERSION,
};
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead};

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Request {
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
        let request: Request = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(err) => {
                emit(Response::<serde_json::Value> {
                    contract_version: CONTRACT_VERSION,
                    ok: false,
                    result: None,
                    error: Some(format!("invalid_request: {err}")),
                });
                continue;
            }
        };
        match request {
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
