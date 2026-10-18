//! Verifies that a configured role can route a child to a different model provider.

use anyhow::Context;
use anyhow::Result;
use codex_core::config::AgentRoleConfig;
use codex_features::Feature;
use codex_model_provider_info::ModelProviderInfo;
use codex_protocol::openai_models::ModelsResponse;
use codex_protocol::protocol::EventMsg;
use codex_protocol::protocol::MultiAgentVersion;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed;
use core_test_support::responses::ev_function_call_with_namespace;
use core_test_support::responses::ev_response_created;
use core_test_support::responses::mount_sse_once_match;
use core_test_support::responses::sse;
use core_test_support::responses::start_mock_server;
use core_test_support::test_codex::test_codex;
use core_test_support::wait_for_event;
use pretty_assertions::assert_eq;
use serde_json::Value;
use serde_json::json;
use std::time::Duration;
use test_case::test_case;

const ROOT_PROMPT: &str = "spawn the provider-specific worker";
const CHILD_PROMPT: &str = "finish the provider-specific task";
const SPAWN_CALL_ID: &str = "spawn-provider-worker";
const ROLE_NAME: &str = "deepseek_worker";
const CHILD_MODEL: &str = "deepseek-flash";

fn body_contains(request: &wiremock::Request, text: &str) -> bool {
    let body = match request
        .headers
        .get("content-encoding")
        .and_then(|encoding| encoding.to_str().ok())
    {
        Some(encoding) if encoding.eq_ignore_ascii_case("zstd") => {
            zstd::stream::decode_all(std::io::Cursor::new(&request.body)).ok()
        }
        _ => Some(request.body.clone()),
    };
    body.and_then(|body| String::from_utf8(body).ok())
        .is_some_and(|body| body.contains(text))
}

fn contains_input_text(value: &Value, expected: &str) -> bool {
    match value {
        Value::Array(values) => values
            .iter()
            .any(|value| contains_input_text(value, expected)),
        Value::Object(map) => {
            (map.get("type").and_then(Value::as_str) == Some("input_text")
                && map
                    .get("text")
                    .and_then(Value::as_str)
                    .is_some_and(|text| text.contains(expected)))
                || map
                    .values()
                    .any(|value| contains_input_text(value, expected))
        }
        _ => false,
    }
}

#[test_case(MultiAgentVersion::V1; "v1")]
#[test_case(MultiAgentVersion::V2; "v2")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn role_routes_child_to_its_configured_provider(version: MultiAgentVersion) -> Result<()> {
    let root_server = start_mock_server().await;
    let child_server = start_mock_server().await;
    let child_base_url = child_server.uri();
    let mut builder = test_codex()
        .with_model("gpt-5.6-sol")
        .with_model_info_override("gpt-5.6-sol", move |model| {
            model.multi_agent_version = Some(version);
        })
        .with_config(move |config| {
            config
                .features
                .enable(Feature::Collab)
                .expect("test config should allow feature update");
            if version == MultiAgentVersion::V2 {
                config.multi_agent_v2.tool_namespace = Some("delegation".to_string());
                config
                    .features
                    .enable(Feature::MultiAgentV2)
                    .expect("test config should allow feature update");
            } else {
                config
                    .features
                    .disable(Feature::MultiAgentV2)
                    .expect("test config should allow feature update");
            }
            config.model_providers.insert(
                "deepseek".to_string(),
                ModelProviderInfo {
                    name: "DeepSeek".to_string(),
                    base_url: Some(child_base_url),
                    request_max_retries: Some(0),
                    stream_max_retries: Some(0),
                    ..Default::default()
                },
            );

            let mut child_model = codex_models_manager::bundled_models_response()
                .expect("bundled model catalog should parse")
                .models
                .into_iter()
                .next()
                .expect("bundled model catalog should not be empty");
            child_model.slug = CHILD_MODEL.to_string();
            child_model.display_name = "DeepSeek V4 Flash".to_string();
            let catalog = ModelsResponse {
                models: vec![child_model],
            };
            let catalog_path = config.codex_home.join("deepseek-models.json");
            std::fs::write(
                &catalog_path,
                serde_json::to_vec(&catalog).expect("serialize child model catalog"),
            )
            .expect("write child model catalog");
            let role_path = config.codex_home.join("deepseek-worker.toml");
            let catalog_path_toml = catalog_path.display().to_string().replace('\\', "/");
            std::fs::write(
                &role_path,
                format!(
                    "model = \"{CHILD_MODEL}\"\nmodel_provider = \"deepseek\"\nmodel_catalog_json = \"{catalog_path_toml}\"\n"
                ),
            )
            .expect("write provider-specific role");
            config.agent_roles.insert(
                ROLE_NAME.to_string(),
                AgentRoleConfig {
                    description: Some("Routes work through DeepSeek".to_string()),
                    config_file: Some(role_path.to_path_buf()),
                    nickname_candidates: None,
                },
            );
        });
    let test = builder.build_with_auto_env(&root_server).await?;
    let mut created_threads = test.thread_manager.subscribe_thread_created();
    let (namespace, spawn_args) = match version {
        MultiAgentVersion::V1 => (
            "multi_agent_v1",
            json!({
                "message": CHILD_PROMPT,
                "agent_type": ROLE_NAME,
                "fork_context": false,
            }),
        ),
        MultiAgentVersion::V2 => (
            "delegation",
            json!({
                "message": CHILD_PROMPT,
                "agent_type": ROLE_NAME,
                "task_name": "provider_worker",
                "fork_turns": "none",
            }),
        ),
        MultiAgentVersion::Disabled => unreachable!("test requires multi-agent support"),
    };

    mount_sse_once_match(
        &root_server,
        |request: &wiremock::Request| {
            body_contains(request, ROOT_PROMPT) && !body_contains(request, SPAWN_CALL_ID)
        },
        sse(vec![
            ev_response_created("root-spawn"),
            ev_function_call_with_namespace(
                SPAWN_CALL_ID,
                namespace,
                "spawn_agent",
                &spawn_args.to_string(),
            ),
            ev_completed("root-spawn"),
        ]),
    )
    .await;
    mount_sse_once_match(
        &root_server,
        |request: &wiremock::Request| {
            body_contains(request, ROOT_PROMPT) && body_contains(request, SPAWN_CALL_ID)
        },
        sse(vec![
            ev_response_created("root-complete"),
            ev_assistant_message("root-message", "worker started"),
            ev_completed("root-complete"),
        ]),
    )
    .await;
    let child_request = mount_sse_once_match(
        &child_server,
        |request: &wiremock::Request| body_contains(request, CHILD_PROMPT),
        sse(vec![
            ev_response_created("child-complete"),
            ev_assistant_message("child-message", "worker completed"),
            ev_completed("child-complete"),
        ]),
    )
    .await;

    test.submit_text_turn(ROOT_PROMPT).await?;
    let child_thread_id = tokio::time::timeout(Duration::from_secs(10), created_threads.recv())
        .await
        .context("timed out waiting for the child thread")??;
    let child = test.thread_manager.get_thread(child_thread_id).await?;
    tokio::time::timeout(
        Duration::from_secs(10),
        wait_for_event(&child, |event| matches!(event, EventMsg::TurnComplete(_))),
    )
    .await
    .context("timed out waiting for the child turn")?;

    assert_eq!(child.config_snapshot().await.model_provider_id, "deepseek");
    let child_body = child_request.single_request().body_json();
    assert_eq!(
        child_body.get("model").and_then(Value::as_str),
        Some(CHILD_MODEL)
    );
    assert!(
        contains_input_text(&child_body, CHILD_PROMPT),
        "child task should be delivered in a provider-compatible input_text item: {child_body}"
    );
    if version == MultiAgentVersion::V2 {
        let input = child_body["input"].as_array().expect("child input items");
        assert!(
            input.iter().all(|item| item["type"] != "agent_message"),
            "third-party providers must receive ordinary messages: {child_body}"
        );
        let task_messages = input
            .iter()
            .filter(|item| contains_input_text(&item["content"], CHILD_PROMPT))
            .map(|item| {
                json!({
                    "type": item["type"],
                    "role": item["role"],
                    "content": item["content"],
                })
            })
            .collect::<Vec<_>>();
        assert_eq!(
            task_messages,
            vec![json!({
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": format!(
                        "Message Type: NEW_TASK\nTask name: /root/provider_worker\nSender: /root\nPayload:\n{CHILD_PROMPT}"
                    ),
                }],
            })]
        );
    }
    Ok(())
}
