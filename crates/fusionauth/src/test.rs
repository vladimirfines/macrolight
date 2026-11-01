use super::*;

#[test]
fn oauth_authorize_url_uses_public_url() {
    let client = FusionAuthClient::new(
        "api-key".into(),
        "client-id".into(),
        "client-secret".into(),
        "http://fusionauth:9011".into(),
        "http://localhost:28011/oauth/redirect".into(),
        "google-client-id".into(),
        "google-client-secret".into(),
    )
    .with_public_url("http://localhost:28005".into());

    let url = client
        .construct_oauth2_authorize_url::<()>("idp-id", None, None)
        .unwrap();

    assert!(url.starts_with("http://localhost:28005/oauth2/authorize?"));
    assert!(!url.contains("fusionauth:9011"));
}

#[test]
fn hosted_login_preserves_oauth_parameters_without_an_identity_provider() {
    let client = FusionAuthClient::new(
        "api-key".into(),
        "client-id".into(),
        "client-secret".into(),
        "http://fusionauth:9011".into(),
        "https://example.com/oauth/callback".into(),
        String::new(),
        String::new(),
    )
    .with_public_url("https://auth.example.com".into());

    let url = Url::parse(
        &client
            .construct_hosted_oauth2_authorize_url(Some("broker-state"))
            .unwrap(),
    )
    .unwrap();
    let query: std::collections::HashMap<_, _> = url.query_pairs().collect();
    assert_eq!(url.host_str(), Some("auth.example.com"));
    assert_eq!(url.path(), "/oauth2/authorize");
    assert_eq!(query["client_id"], "client-id");
    assert_eq!(query["redirect_uri"], "https://example.com/oauth/callback");
    assert_eq!(query["response_type"], "code");
    assert_eq!(query["scope"], "openid profile email offline_access");
    assert_eq!(query["access_type"], "offline");
    assert_eq!(query["state"], "\"broker-state\"");
    assert!(!query.contains_key("idp_hint"));
    assert!(!query.contains_key("login_hint"));

    let url = Url::parse(
        &client
            .construct_oauth2_authorize_url("idp-id", Some("example.com"), Some("broker-state"))
            .unwrap(),
    )
    .unwrap();
    let query: std::collections::HashMap<_, _> = url.query_pairs().collect();
    assert_eq!(query["idp_hint"], "idp-id");
    assert_eq!(query["login_hint"], "example.com");
    assert_eq!(query["state"], "\"broker-state\"");
}

#[test]
fn invalid_public_login_url_returns_an_error() {
    let client = FusionAuthClient::new(
        "api-key".into(),
        "client-id".into(),
        "client-secret".into(),
        "not a URL".into(),
        "https://example.com/oauth/callback".into(),
        String::new(),
        String::new(),
    );
    assert!(
        client
            .construct_hosted_oauth2_authorize_url::<()>(None)
            .is_err()
    );
}
