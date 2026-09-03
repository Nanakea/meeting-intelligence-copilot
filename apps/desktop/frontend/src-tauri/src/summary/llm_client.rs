use reqwest::{header, Client};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::Duration;
use tokio_util::sync::CancellationToken;
use tracing::info;

const REQUEST_TIMEOUT_DURATION: Duration = Duration::from_secs(300);

// Generic structure for OpenAI-compatible API chat messages
#[derive(Debug, Serialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

// Generic structure for OpenAI-compatible API chat requests
#[derive(Debug, Serialize)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_p: Option<f32>,
}

// Generic structure for OpenAI-compatible API chat responses
#[derive(Deserialize, Debug)]
pub struct ChatResponse {
    pub choices: Vec<Choice>,
}

#[derive(Deserialize, Debug)]
pub struct Choice {
    pub message: MessageContent,
}

#[derive(Deserialize, Debug)]
pub struct MessageContent {
    pub content: String,
}

// Claude-specific request structure
#[derive(Debug, Serialize)]
pub struct ClaudeRequest {
    pub model: String,
    pub max_tokens: u32,
    pub system: String,
    pub messages: Vec<ChatMessage>,
}

// Claude-specific response structure
#[derive(Deserialize, Debug)]
pub struct ClaudeChatResponse {
    pub content: Vec<ClaudeChatContent>,
}

#[derive(Deserialize, Debug)]
pub struct ClaudeChatContent {
    pub text: String,
}

/// LLM Provider enumeration for multi-provider support
#[derive(Debug, Clone, PartialEq)]
pub enum LLMProvider {
    OpenAI,
    Claude,
    Groq,
    Ollama,
    OpenRouter,
    BuiltInAI,
    CustomOpenAI,
}

impl LLMProvider {
    /// Parse provider from string (case-insensitive)
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s.to_lowercase().as_str() {
            "ollama" => Ok(Self::Ollama),
            "builtin-ai" | "local-llama" | "localllama" => Ok(Self::BuiltInAI),
            _ => Err("Only local summary providers are enabled in this build".to_string()),
        }
    }
}

fn validated_ollama_endpoint(raw: Option<&str>) -> Result<String, String> {
    let candidate = raw
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("http://127.0.0.1:11434");
    let parsed = url::Url::parse(candidate)
        .map_err(|_| "Ollama endpoint must be a valid loopback HTTP URL".to_string())?;
    let loopback_host = match parsed.host() {
        Some(url::Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    };
    if parsed.scheme() != "http"
        || !loopback_host
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !matches!(parsed.path(), "" | "/")
    {
        return Err("Ollama endpoint must be a plain loopback HTTP origin".to_string());
    }
    Ok(candidate.trim_end_matches('/').to_string())
}

/// Generates a summary using the specified LLM provider
///
/// # Arguments
/// * `client` - Reqwest HTTP client (reused for performance)
/// * `provider` - The LLM provider to use
/// * `model_name` - The specific model to use (e.g., "gpt-4", "claude-3-opus")
/// * `api_key` - API key for the provider (not needed for Ollama)
/// * `system_prompt` - System instructions for the LLM
/// * `user_prompt` - User query/content to process
/// * `ollama_endpoint` - Optional custom Ollama endpoint (defaults to localhost:11434)
/// * `custom_openai_endpoint` - Optional custom OpenAI-compatible endpoint
/// * `max_tokens` - Optional max tokens (for CustomOpenAI provider)
/// * `temperature` - Optional temperature (for CustomOpenAI provider)
/// * `top_p` - Optional top_p (for CustomOpenAI provider)
/// * `app_data_dir` - Optional app data directory (for BuiltInAI provider)
/// * `cancellation_token` - Optional token to cancel the request
///
/// # Returns
/// The generated summary text or an error message
pub async fn generate_summary(
    client: &Client,
    provider: &LLMProvider,
    model_name: &str,
    api_key: &str,
    system_prompt: &str,
    user_prompt: &str,
    ollama_endpoint: Option<&str>,
    _custom_openai_endpoint: Option<&str>,
    max_tokens: Option<u32>,
    temperature: Option<f32>,
    top_p: Option<f32>,
    app_data_dir: Option<&PathBuf>,
    cancellation_token: Option<&CancellationToken>,
) -> Result<String, String> {
    // Check if cancelled before starting
    if let Some(token) = cancellation_token {
        if token.is_cancelled() {
            return Err("Summary generation was cancelled".to_string());
        }
    }

    // Handle BuiltInAI provider separately (uses local sidecar, no HTTP API)
    if provider == &LLMProvider::BuiltInAI {
        let app_data_dir = app_data_dir
            .ok_or_else(|| "app_data_dir is required for BuiltInAI provider".to_string())?;

        return crate::summary::summary_engine::generate_with_builtin(
            app_data_dir,
            model_name,
            system_prompt,
            user_prompt,
            cancellation_token,
        )
        .await
        .map_err(|e| e.to_string());
    }

    let (api_url, mut headers) = match provider {
        LLMProvider::OpenAI
        | LLMProvider::Claude
        | LLMProvider::Groq
        | LLMProvider::OpenRouter
        | LLMProvider::CustomOpenAI => {
            return Err("Cloud and custom summary providers are disabled in this build".to_string())
        }
        LLMProvider::Ollama => {
            let host = validated_ollama_endpoint(ollama_endpoint)?;
            (
                format!("{}/v1/chat/completions", host),
                header::HeaderMap::new(),
            )
        }
        LLMProvider::BuiltInAI => {
            // This case is handled earlier with early returns
            unreachable!("BuiltInAI is handled before this match statement")
        }
    };

    // Add authorization header for non-Claude providers
    if provider != &LLMProvider::Claude {
        headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {}", api_key)
                .parse()
                .map_err(|_| "Invalid authorization header".to_string())?,
        );
    }
    headers.insert(
        header::CONTENT_TYPE,
        "application/json"
            .parse()
            .map_err(|_| "Invalid content type".to_string())?,
    );

    // Build request body based on provider
    let request_body = if provider != &LLMProvider::Claude {
        // For CustomOpenAI, apply optional parameters if provided
        let (max_tokens_val, temperature_val, top_p_val) = if provider == &LLMProvider::CustomOpenAI
        {
            (max_tokens, temperature, top_p)
        } else {
            (None, None, None)
        };

        serde_json::json!(ChatRequest {
            model: model_name.to_string(),
            messages: vec![
                ChatMessage {
                    role: "system".to_string(),
                    content: system_prompt.to_string(),
                },
                ChatMessage {
                    role: "user".to_string(),
                    content: user_prompt.to_string(),
                }
            ],
            max_tokens: max_tokens_val,
            temperature: temperature_val,
            top_p: top_p_val,
        })
    } else {
        serde_json::json!(ClaudeRequest {
            system: system_prompt.to_string(),
            model: model_name.to_string(),
            max_tokens: 2048,
            messages: vec![ChatMessage {
                role: "user".to_string(),
                content: user_prompt.to_string(),
            }]
        })
    };

    info!(
        "🐞 LLM Request to {}: model={}",
        provider_name(provider),
        model_name
    );

    // Send request with timeout and cancellation support
    let request_future = client
        .post(api_url)
        .headers(headers)
        .json(&request_body)
        .timeout(REQUEST_TIMEOUT_DURATION)
        .send();

    // Use tokio::select to race between cancellation and request completion
    let response = if let Some(token) = cancellation_token {
        tokio::select! {
            result = request_future => {
                result.map_err(|e| {
                    if e.is_timeout() {
                        format!("LLM request timed out after 60 seconds")
                    } else {
                        format!("Failed to send request to LLM: {}", e)
                    }
                })?
            }
            _ = token.cancelled() => {
                return Err("Summary generation was cancelled".to_string());
            }
        }
    } else {
        request_future.await.map_err(|e| {
            if e.is_timeout() {
                format!("LLM request timed out after 60 seconds")
            } else {
                format!("Failed to send request to LLM: {}", e)
            }
        })?
    };

    if !response.status().is_success() {
        let error_body = response
            .text()
            .await
            .unwrap_or_else(|_| "Unknown error".to_string());
        return Err(format!("LLM API request failed: {}", error_body));
    }

    // Parse response based on provider
    if provider == &LLMProvider::Claude {
        let chat_response = response
            .json::<ClaudeChatResponse>()
            .await
            .map_err(|e| format!("Failed to parse LLM response: {}", e))?;

        info!("🐞 LLM Response received from Claude");

        let content = chat_response
            .content
            .get(0)
            .ok_or("No content in LLM response")?
            .text
            .trim();
        Ok(content.to_string())
    } else {
        let chat_response = response
            .json::<ChatResponse>()
            .await
            .map_err(|e| format!("Failed to parse LLM response: {}", e))?;

        info!("🐞 LLM Response received from {}", provider_name(provider));

        let content = chat_response
            .choices
            .get(0)
            .ok_or("No content in LLM response")?
            .message
            .content
            .trim();
        Ok(content.to_string())
    }
}

#[cfg(test)]
mod local_only_tests {
    use super::{validated_ollama_endpoint, LLMProvider};

    #[test]
    fn summary_provider_selection_is_local_only() {
        assert_eq!(LLMProvider::from_str("ollama"), Ok(LLMProvider::Ollama));
        assert_eq!(
            LLMProvider::from_str("builtin-ai"),
            Ok(LLMProvider::BuiltInAI)
        );
        for remote in ["openai", "claude", "groq", "openrouter", "custom-openai"] {
            assert!(LLMProvider::from_str(remote).is_err());
        }
    }

    #[test]
    fn ollama_endpoint_accepts_only_plain_loopback_http_origins() {
        for allowed in [
            "http://localhost:11434",
            "http://127.0.0.1:11434/",
            "http://[::1]:11434",
        ] {
            assert!(
                validated_ollama_endpoint(Some(allowed)).is_ok(),
                "{allowed}"
            );
        }
        for rejected in [
            "https://localhost:11434",
            "http://example.com:11434",
            "http://user@localhost:11434",
            "http://localhost:11434/v1",
            "http://localhost:11434?token=secret",
        ] {
            assert!(
                validated_ollama_endpoint(Some(rejected)).is_err(),
                "{rejected}"
            );
        }
    }
}

/// Helper function to get provider name for logging
fn provider_name(provider: &LLMProvider) -> &str {
    match provider {
        LLMProvider::OpenAI => "OpenAI",
        LLMProvider::Claude => "Claude",
        LLMProvider::Groq => "Groq",
        LLMProvider::Ollama => "Ollama",
        LLMProvider::BuiltInAI => "Built-in AI",
        LLMProvider::OpenRouter => "OpenRouter",
        LLMProvider::CustomOpenAI => "Custom OpenAI",
    }
}
