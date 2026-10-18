//! Optional model-to-provider routing for the desktop model picker.
//!
//! Providers remain fixed for each task. This only selects the provider at task
//! creation, and rejects incompatible model changes before submitting a turn.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::io::ErrorKind;

use codex_app_server_protocol::JSONRPCErrorError;
use codex_core::config::Config;
use serde::Deserialize;

use crate::error_code::invalid_request;

#[derive(Deserialize)]
pub(crate) struct DesktopProviderRoutes {
    version: u32,
    default_provider: String,
    model_providers: BTreeMap<String, String>,
}

impl DesktopProviderRoutes {
    pub(crate) async fn prepare_task_catalog(config: &mut Config) -> Result<(), JSONRPCErrorError> {
        if let Some(routes) = Self::load(config).await?
            && (config.model_provider_id == routes.default_provider
                || routes
                    .model_providers
                    .values()
                    .any(|provider| provider == &config.model_provider_id))
            && let Some(catalog) = config.model_catalog.as_mut()
        {
            // Keep the global picker intact, but only advertise this task's
            // provider models to its ordinary subagents and internal callers.
            catalog.models.retain(|model| {
                routes
                    .model_providers
                    .get(&model.slug)
                    .unwrap_or(&routes.default_provider)
                    == &config.model_provider_id
                    || config.model.as_deref() == Some(model.slug.as_str())
            });
        }
        Ok(())
    }

    pub(crate) async fn load(config: &Config) -> Result<Option<Self>, JSONRPCErrorError> {
        let path = config.codex_home.join("desktop-model-providers.json");
        let bytes = match tokio::fs::read(path).await {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) => {
                return Err(invalid_request(format!(
                    "Cannot read desktop provider routes: {error}"
                )));
            }
        };
        if bytes.len() > 65_536 {
            return Err(invalid_request("Desktop provider routes exceed 64 KiB"));
        }
        let routes: Self = serde_json::from_slice(&bytes).map_err(|error| {
            invalid_request(format!("Invalid desktop provider routes: {error}"))
        })?;
        if routes.version != 1 {
            return Err(invalid_request(
                "Unsupported desktop provider routes version",
            ));
        }
        for provider in routes
            .model_providers
            .values()
            .chain(std::iter::once(&routes.default_provider))
        {
            if !config.model_providers.contains_key(provider) {
                return Err(invalid_request(format!(
                    "Desktop model provider `{provider}` is not configured"
                )));
            }
        }
        Ok(Some(routes))
    }

    pub(crate) fn start_provider(
        &self,
        model: Option<&str>,
        explicit: Option<String>,
    ) -> Option<String> {
        explicit.or_else(|| model.and_then(|model| self.model_providers.get(model).cloned()))
    }

    pub(crate) fn visible_providers(&self, configured_provider: &str) -> Vec<String> {
        self.model_providers
            .values()
            .cloned()
            .chain([
                self.default_provider.clone(),
                configured_provider.to_string(),
            ])
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    pub(crate) fn validate_model_change(
        &self,
        model: &str,
        active_provider: &str,
    ) -> Result<(), JSONRPCErrorError> {
        let mapped = self.model_providers.get(model);
        let active_is_managed = active_provider == self.default_provider
            || self
                .model_providers
                .values()
                .any(|provider| provider == active_provider);
        if (mapped.is_some() || active_is_managed)
            && mapped.unwrap_or(&self.default_provider) != active_provider
        {
            return Err(invalid_request(format!(
                "Model `{model}` uses provider `{}` but this task uses `{active_provider}`. Start a new task with the selected model to change providers; models within the same provider can still be switched.",
                mapped.unwrap_or(&self.default_provider)
            )));
        }
        Ok(())
    }
}
