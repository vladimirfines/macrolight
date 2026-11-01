use super::*;

#[tokio::test]
async fn an_unconfigured_catalog_refuses_every_slug() {
    let catalog = PipedreamMcpAppCatalog::new(None);
    let error = catalog
        .is_connectable_app("linear")
        .await
        .expect_err("no directory");
    assert!(matches!(error, BotError::Unavailable(message) if message.contains("not configured")));
}

fn catalog_env(
    client_id: Option<&'static str>,
    client_secret: Option<&'static str>,
    project_id: Option<&'static str>,
) -> PipedreamCatalogEnv {
    PipedreamCatalogEnv {
        pipedream_client_id: client_id.map(PipedreamClientId::new_testing),
        pipedream_client_secret: client_secret.map(PipedreamClientSecret::new_testing),
        pipedream_project_id: project_id.map(PipedreamProjectId::new_testing),
        pipedream_environment: Some(PipedreamEnvironment::new_testing("development")),
        pipedream_api_url: None,
        pipedream_mcp_url: None,
    }
}

#[test]
fn absent_or_blank_credentials_disable_the_catalog() {
    for env in [
        catalog_env(None, None, None),
        catalog_env(Some(""), Some(" "), Some("\t")),
    ] {
        assert!(pipedream_client(env).unwrap().is_none());
    }
}

#[test]
fn partial_credentials_are_rejected_without_exposing_the_secret() {
    for mask in 1..7 {
        let error = pipedream_client(catalog_env(
            (mask & 1 != 0).then_some("client"),
            (mask & 2 != 0).then_some("test-secret-value"),
            (mask & 4 != 0).then_some("project"),
        ))
        .err()
        .expect("a partial credential set is invalid");
        assert!(error.to_string().contains("set all of PIPEDREAM_CLIENT_ID"));
        assert!(!error.to_string().contains("test-secret-value"));
    }
    assert!(pipedream_client(catalog_env(Some("client"), Some(" "), Some("project"))).is_err());
}

#[test]
fn complete_credentials_build_the_catalog_client_without_network_access() {
    assert!(
        pipedream_client(catalog_env(
            Some("client"),
            Some("test-secret-value"),
            Some("project")
        ))
        .unwrap()
        .is_some()
    );
}
