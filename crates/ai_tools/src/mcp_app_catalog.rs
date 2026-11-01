//! Pipedream app directory used when an agent pins MCP servers.

use std::sync::Arc;

use anyhow::Context;
use bots::domain::ports::{BotError, McpAppCatalog};
use macro_env::Environment;
use macro_env_var::maybe_env_var;
use pipedream_mcp::domain::ports::ConnectorDirectory;
use pipedream_mcp::outbound::api::{
    DEFAULT_API_URL, DEFAULT_MCP_URL, PipedreamClient, PipedreamConfig,
};

maybe_env_var! {
    struct PipedreamCatalogEnv {
        /// OAuth client ID for the Pipedream API.
        PipedreamClientId,
        /// OAuth client secret for the Pipedream API.
        PipedreamClientSecret,
        /// The Pipedream Connect project ID (`proj_...`).
        PipedreamProjectId,
        /// The Pipedream project environment (`development` or `production`).
        PipedreamEnvironment,
        /// Base URL of the Pipedream API.
        PipedreamApiUrl,
        /// Base URL of the Pipedream MCP server, shared with execution.
        PipedreamMcpUrl,
    }
}

/// A Pipedream client when this process has the credentials, otherwise `None`.
///
/// Missing or blank credentials disable the catalog. A partial set fails
/// configuration rather than attempting a request with invalid credentials.
pub fn pipedream_client_from_env() -> anyhow::Result<Option<Arc<PipedreamClient>>> {
    pipedream_client(PipedreamCatalogEnv::new())
}

fn pipedream_client(env: PipedreamCatalogEnv) -> anyhow::Result<Option<Arc<PipedreamClient>>> {
    let (client_id, client_secret, project_id) = match (
        env.pipedream_client_id
            .as_ref()
            .and_then(|value| value.value())
            .filter(|value| !value.trim().is_empty()),
        env.pipedream_client_secret
            .as_ref()
            .and_then(|value| value.value())
            .filter(|value| !value.trim().is_empty()),
        env.pipedream_project_id
            .as_ref()
            .and_then(|value| value.value())
            .filter(|value| !value.trim().is_empty()),
    ) {
        (Some(client_id), Some(client_secret), Some(project_id)) => {
            (client_id, client_secret, project_id)
        }
        (None, None, None) => return Ok(None),
        _ => anyhow::bail!(
            "set all of PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET and PIPEDREAM_PROJECT_ID, or leave all unset"
        ),
    };

    let environment = env
        .pipedream_environment
        .as_ref()
        .and_then(|value| value.value())
        .map(str::to_owned)
        .unwrap_or_else(|| match Environment::new_or_prod() {
            Environment::Production => "production".to_owned(),
            Environment::Develop | Environment::Local => "development".to_owned(),
        });
    let api_url = env
        .pipedream_api_url
        .as_ref()
        .and_then(|value| value.value())
        .unwrap_or(DEFAULT_API_URL)
        .to_owned();

    let client = PipedreamClient::new(PipedreamConfig {
        client_id: client_id.to_owned(),
        client_secret: client_secret.to_owned(),
        project_id: project_id.to_owned(),
        environment,
        allowed_origins: Vec::new(),
        api_url,
        mcp_url: env
            .pipedream_mcp_url
            .as_ref()
            .and_then(|value| value.value())
            .unwrap_or(DEFAULT_MCP_URL)
            .to_owned(),
    })
    .context("building Pipedream client for MCP app lookup")?;
    Ok(Some(Arc::new(client)))
}

/// Confirms agent MCP slugs against Pipedream's app directory.
///
/// With no client, every slug is refused: an unverified app is not stored.
#[derive(Clone)]
pub struct PipedreamMcpAppCatalog {
    client: Option<Arc<PipedreamClient>>,
}

impl PipedreamMcpAppCatalog {
    /// Consult `client`, or refuse every slug when Pipedream is not configured.
    pub fn new(client: Option<Arc<PipedreamClient>>) -> Self {
        Self { client }
    }
}

impl std::fmt::Debug for PipedreamMcpAppCatalog {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PipedreamMcpAppCatalog")
            .field("configured", &self.client.is_some())
            .finish()
    }
}

impl McpAppCatalog for PipedreamMcpAppCatalog {
    async fn is_connectable_app(&self, slug: &str) -> Result<bool, BotError> {
        let Some(client) = &self.client else {
            return Err(BotError::Unavailable(format!(
                "cannot verify MCP app {slug}: Pipedream is not configured"
            )));
        };
        match client.retrieve(slug).await {
            Ok(Some(_)) => Ok(true),
            Ok(None) => Ok(false),
            Err(error) => {
                tracing::warn!(%slug, %error, "MCP app lookup failed");
                Err(BotError::Unavailable(format!(
                    "could not verify MCP app {slug}"
                )))
            }
        }
    }
}

#[cfg(test)]
mod test;
