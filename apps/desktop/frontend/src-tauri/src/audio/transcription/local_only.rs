//! Local-only transport guards for the Meeting Intelligence Copilot bridge.

use url::{Host, Url};

pub const DEFAULT_LOCAL_COPILOT_URL: &str = "http://127.0.0.1:8000";

fn is_loopback_host(url: &Url) -> bool {
    match url.host() {
        Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
        Some(Host::Ipv4(host)) => host == std::net::Ipv4Addr::LOCALHOST,
        Some(Host::Ipv6(host)) => host.is_loopback(),
        None => false,
    }
}

/// Return a safe copilot backend base URL.
///
/// The intelligence bridge carries transcript text, so an override is valid
/// only when it is an HTTP URL whose host is loopback and whose path/query do
/// not add another routing surface. Invalid or empty values use the local
/// default rather than sending data to a remote endpoint.
pub fn local_copilot_url_or_default(configured: Option<&str>) -> String {
    let Some(raw) = configured.map(str::trim).filter(|value| !value.is_empty()) else {
        return DEFAULT_LOCAL_COPILOT_URL.to_string();
    };

    let Ok(parsed) = Url::parse(raw) else {
        return DEFAULT_LOCAL_COPILOT_URL.to_string();
    };

    let valid_path = parsed.path().is_empty() || parsed.path() == "/";
    let valid = parsed.scheme() == "http"
        && parsed.username().is_empty()
        && parsed.password().is_none()
        && parsed.query().is_none()
        && parsed.fragment().is_none()
        && valid_path
        && is_loopback_host(&parsed);

    if valid {
        raw.trim_end_matches('/').to_string()
    } else {
        DEFAULT_LOCAL_COPILOT_URL.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::{local_copilot_url_or_default, DEFAULT_LOCAL_COPILOT_URL};

    #[test]
    fn accepts_loopback_http_urls() {
        assert_eq!(
            local_copilot_url_or_default(Some("http://127.0.0.1:8123")),
            "http://127.0.0.1:8123"
        );
        assert_eq!(
            local_copilot_url_or_default(Some("http://localhost:8000/")),
            "http://localhost:8000"
        );
        assert_eq!(
            local_copilot_url_or_default(Some("http://[::1]:8000")),
            "http://[::1]:8000"
        );
    }

    #[test]
    fn rejects_remote_https_and_malformed_urls_to_safe_default() {
        for value in [
            "https://example.com",
            "http://192.168.1.20:8000",
            "http://localhost.evil.example:8000",
            "not a URL",
            "",
        ] {
            assert_eq!(
                local_copilot_url_or_default(Some(value)),
                DEFAULT_LOCAL_COPILOT_URL
            );
        }
        assert_eq!(
            local_copilot_url_or_default(None),
            DEFAULT_LOCAL_COPILOT_URL
        );
    }

    #[test]
    fn rejects_extra_path_query_and_credentials() {
        for value in [
            "http://127.0.0.1:8000/other",
            "http://127.0.0.1:8000/?next=https://example.com",
            "http://user@127.0.0.1:8000",
        ] {
            assert_eq!(
                local_copilot_url_or_default(Some(value)),
                DEFAULT_LOCAL_COPILOT_URL
            );
        }
    }
}
