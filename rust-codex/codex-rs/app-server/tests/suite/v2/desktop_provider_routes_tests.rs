use anyhow::Result;
use app_test_support::TestAppServer;
use app_test_support::create_mock_responses_server_repeating_assistant;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::ThreadListParams;
use codex_app_server_protocol::ThreadListResponse;
use codex_app_server_protocol::ThreadStartParams;
use codex_app_server_protocol::TurnStartParams;
use codex_app_server_protocol::TurnStatus;
use codex_app_server_protocol::UserInput;
use serde_json::json;
use tempfile::TempDir;

#[tokio::test]
async fn desktop_provider_routes_keep_requests_credentials_and_history_separate() -> Result<()> {
    let primary = create_mock_responses_server_repeating_assistant("PRIMARY_OK").await;
    let external = create_mock_responses_server_repeating_assistant("EXTERNAL_OK").await;
    let home = TempDir::new()?;
    let workspace = TempDir::new()?;
    let mut catalog = codex_models_manager::bundled_models_response()?;
    let mut primary_model = catalog.models.remove(0);
    primary_model.slug = "gpt-5.5".into();
    primary_model.description = Some("PRIMARY_PROVIDER_MODEL_MARKER".into());
    let mut external_model = primary_model.clone();
    external_model.slug = "deepseek-flash".into();
    external_model.description = Some("EXTERNAL_PROVIDER_MODEL_MARKER".into());
    catalog.models = vec![primary_model, external_model];
    let catalog_path = home.path().join("models.json");
    std::fs::write(&catalog_path, serde_json::to_vec(&catalog)?)?;
    let config = format!(
        "model = \"gpt-5.5\"\nmodel_provider = \"primary\"\n[model_providers.primary]\nname = \"primary\"\nbase_url = \"{}/v1\"\nexperimental_bearer_token = \"primary-key\"\nwire_api = \"responses\"\n[model_providers.external]\nname = \"external\"\nbase_url = \"{}/v1\"\nexperimental_bearer_token = \"external-key\"\nwire_api = \"responses\"\n",
        primary.uri(),
        external.uri()
    );
    let catalog_setting =
        serde_json::to_string(&catalog_path.to_string_lossy().replace('\\', "/"))?;
    std::fs::write(
        home.path().join("config.toml"),
        format!(
            "model_catalog_json = {catalog_setting}\n{config}\n[features]\nmulti_agent = true\nmulti_agent_v2 = {{ enabled = true, tool_namespace = \"delegation\" }}\n"
        ),
    )?;
    std::fs::write(
        home.path().join("desktop-model-providers.json"),
        serde_json::to_vec(&json!({
            "version": 1, "default_provider": "primary", "model_providers": { "deepseek-flash": "external" }
        }))?,
    )?;
    let mut app = TestAppServer::builder()
        .with_codex_home(home.path())
        .build_initialized()
        .await?;
    let mut ids = Vec::new();
    for (model, provider) in [("deepseek-flash", "external"), ("gpt-5.5", "primary")] {
        let started = app
            .start_thread(ThreadStartParams {
                model: Some(model.into()),
                cwd: Some(workspace.path().to_string_lossy().into_owned()),
                ..Default::default()
            })
            .await?;
        assert_eq!(started.model_provider, provider);
        let completed = app
            .start_turn_and_wait_for_completion(TurnStartParams {
                thread_id: started.thread.id.clone(),
                model: Some(model.into()),
                input: vec![UserInput::Text {
                    text: "Reply with OK".into(),
                    text_elements: Vec::new(),
                }],
                ..Default::default()
            })
            .await?;
        assert_eq!(completed.turn.status, TurnStatus::Completed);
        ids.push(started.thread.id);
    }
    for (server, key, expected_model) in [
        (&primary, "primary-key", "gpt-5.5"),
        (&external, "external-key", "deepseek-flash"),
    ] {
        let requests = server.received_requests().await.unwrap();
        let requests: Vec<_> = requests
            .iter()
            .filter(|request| {
                request.method == "POST" && request.url.path().ends_with("/responses")
            })
            .collect();
        assert_eq!(requests.len(), 1);
        assert_eq!(
            requests[0].headers.get("authorization").unwrap().to_str()?,
            format!("Bearer {key}")
        );
        assert_eq!(
            requests[0].body_json::<serde_json::Value>()?["model"],
            expected_model
        );
        let body = String::from_utf8_lossy(&requests[0].body);
        let other_marker = if expected_model == "deepseek-flash" {
            "PRIMARY_PROVIDER_MODEL_MARKER"
        } else {
            "EXTERNAL_PROVIDER_MODEL_MARKER"
        };
        assert!(
            !body.contains(other_marker),
            "Ordinary subagents must only see models on their own provider"
        );
    }
    let list_params: ThreadListParams = serde_json::from_value(json!({}))?;
    let listed: ThreadListResponse = app
        .request(|request_id| ClientRequest::ThreadList {
            request_id,
            params: list_params,
        })
        .await?;
    for id in &ids {
        assert!(listed.data.iter().any(|thread| &thread.id == id));
    }
    // Both directions must fail before any model request reaches either service.
    for (id, model) in [(&ids[0], "gpt-5.5"), (&ids[1], "deepseek-flash")] {
        let request_id = app
            .send_turn_start_request(TurnStartParams {
                thread_id: id.clone(),
                model: Some(model.into()),
                input: vec![UserInput::Text {
                    text: "Do not send this across providers".into(),
                    text_elements: Vec::new(),
                }],
                ..Default::default()
            })
            .await?;
        let error = app
            .read_stream_until_error_message(RequestId::Integer(request_id))
            .await?;
        assert!(error.error.message.contains("Start a new task"));
    }
    Ok(())
}
