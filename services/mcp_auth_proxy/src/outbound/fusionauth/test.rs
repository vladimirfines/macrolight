use super::*;

#[test]
fn provider_initializes_without_google_or_a_reachable_identity_api() {
    let client = fusionauth::FusionAuthClient::new(
        "api-key".into(),
        "client-id".into(),
        "client-secret".into(),
        "http://127.0.0.1:1".into(),
        "https://example.com/oauth/callback".into(),
        String::new(),
        String::new(),
    )
    .with_public_url("https://auth.example.com".into());

    let provider = FusionAuthOAuthProvider::new(client);
    let url = url::Url::parse(&provider.construct_authorize_url("state").unwrap()).unwrap();
    assert_eq!(url.host_str(), Some("auth.example.com"));
    assert!(!url.query_pairs().any(|(name, _)| name == "idp_hint"));
}
