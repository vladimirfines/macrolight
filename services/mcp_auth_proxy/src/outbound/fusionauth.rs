//! FusionAuth adapter for the MCP OAuth broker.

use std::sync::Arc;
use tracing::Instrument;

#[cfg(test)]
mod test;

use crate::domain::{
    models::{RefreshToken, UpstreamTokens},
    ports::{OAuthProvider, UpstreamRevocationFuture, UpstreamTokensFuture},
};

/// FusionAuth-backed OAuth provider for the MCP auth proxy.
#[derive(Clone)]
pub struct FusionAuthOAuthProvider {
    client: Arc<fusionauth::FusionAuthClient>,
}

impl FusionAuthOAuthProvider {
    /// Creates a provider using FusionAuth's configured login page.
    pub fn new(client: fusionauth::FusionAuthClient) -> Self {
        Self {
            client: Arc::new(client),
        }
    }
}

impl OAuthProvider for FusionAuthOAuthProvider {
    #[tracing::instrument(skip_all, err)]
    fn construct_authorize_url(&self, state: &str) -> anyhow::Result<String> {
        self.client
            .construct_hosted_oauth2_authorize_url(Some(state.to_owned()))
    }

    fn exchange_authorization_code<'a>(&'a self, code: &'a str) -> UpstreamTokensFuture<'a> {
        let span = tracing::debug_span!("FusionAuthOAuthProvider::exchange_authorization_code");
        Box::pin(
            async move {
                let grant = self
                    .client
                    .complete_authorization_code_grant(code)
                    .await
                    .map_err(anyhow::Error::from)?;

                Ok(UpstreamTokens {
                    access_token: grant.access_token.into(),
                    refresh_token: grant.refresh_token.into(),
                    expires_in: grant.expires_in,
                })
            }
            .instrument(span),
        )
    }

    fn refresh_access_token<'a>(
        &'a self,
        refresh_token: &'a RefreshToken,
    ) -> UpstreamTokensFuture<'a> {
        let span = tracing::debug_span!("FusionAuthOAuthProvider::refresh_access_token");
        Box::pin(
            async move {
                let grant = self
                    .client
                    .complete_refresh_token_grant(refresh_token.as_str())
                    .await
                    .map_err(anyhow::Error::from)?;

                Ok(UpstreamTokens {
                    access_token: grant.access_token.into(),
                    refresh_token: grant.refresh_token.into(),
                    expires_in: grant.expires_in,
                })
            }
            .instrument(span),
        )
    }

    fn revoke_refresh_token<'a>(
        &'a self,
        refresh_token: &'a RefreshToken,
    ) -> UpstreamRevocationFuture<'a> {
        let span = tracing::debug_span!("FusionAuthOAuthProvider::revoke_refresh_token");
        Box::pin(
            async move {
                self.client
                    .revoke_refresh_token(refresh_token.as_str())
                    .await
                    .map_err(anyhow::Error::from)
            }
            .instrument(span),
        )
    }
}
