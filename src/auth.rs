use std::path::Path;
use std::time::{Duration, SystemTime};
use anyhow::{Context, Result, bail};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tokio::time::sleep;

const DEVICE_CODE_URL: &str = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode";
const TOKEN_URL: &str = "https://login.microsoftonline.com/common/oauth2/v2.0/token";
const SCOPE: &str = "Presence.Read Presence.Read.All offline_access User.Read openid profile";

#[derive(Debug, Deserialize)]
struct DeviceCodeResponse {
    device_code: String,
    user_code: String,
    verification_uri: String,
    message: String,
    expires_in: u64,
    interval: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_in: u64,
    #[serde(skip)]
    pub obtained_at: Option<SystemTime>,
}

#[derive(Debug, Serialize, Deserialize)]
struct CachedToken {
    access_token: String,
    refresh_token: String,
    expires_at: i64,
}

impl TokenResponse {
    pub fn is_expired(&self) -> bool {
        match self.obtained_at {
            Some(t) => {
                let elapsed = t.elapsed().unwrap_or(Duration::MAX);
                elapsed >= Duration::from_secs(self.expires_in.saturating_sub(300))
            }
            None => true,
        }
    }
}

pub async fn authenticate(client_id: &str, cache_path: &Path) -> Result<TokenResponse> {
    if cache_path.exists() {
        let cached = load_cached_token(cache_path)?;
        if cached.expires_at > chrono::Utc::now().timestamp() {
            tracing::info!("Using cached access token");
            return Ok(TokenResponse {
                access_token: cached.access_token,
                refresh_token: cached.refresh_token,
                expires_in: (cached.expires_at - chrono::Utc::now().timestamp()).max(0) as u64,
                obtained_at: Some(SystemTime::now()),
            });
        }
        tracing::info!("Access token expired, refreshing...");
        return refresh_token(client_id, &cached.refresh_token, cache_path).await;
    }

    device_code_flow(client_id, cache_path).await
}

async fn device_code_flow(client_id: &str, cache_path: &Path) -> Result<TokenResponse> {
    let client = Client::new();

    let params = [
        ("client_id", client_id),
        ("scope", SCOPE),
    ];

    let res = client
        .post(DEVICE_CODE_URL)
        .form(&params)
        .send()
        .await
        .context("Failed to request device code")?;

    let device_code: DeviceCodeResponse = res
        .json()
        .await
        .context("Failed to parse device code response")?;

    println!("\n=== Microsoft Authentication Required ===");
    println!("{}", device_code.message);
    println!("Or manually open: {}", device_code.verification_uri);
    println!("Code: {}", device_code.user_code);
    println!("=========================================\n");

    let poll_interval = Duration::from_secs(device_code.interval.max(5));
    let expires_in = Duration::from_secs(device_code.expires_in);
    let start = std::time::Instant::now();

    let token_params = [
        ("grant_type", "urn:ietf:params:oauth:grant-type:device_code"),
        ("client_id", client_id),
        ("device_code", &device_code.device_code),
    ];

    loop {
        sleep(poll_interval).await;

        if start.elapsed() > expires_in {
            bail!("Device code expired. Please try again.");
        }

        let res = client
            .post(TOKEN_URL)
            .form(&token_params)
            .send()
            .await
            .context("Failed to poll for token")?;

        let status = res.status();
        let body = res.text().await.context("Failed to read token response body")?;

        if status.is_success() {
            let mut token: TokenResponse = serde_json::from_str(&body)
                .context("Failed to parse token response")?;
            token.obtained_at = Some(SystemTime::now());
            save_cached_token(cache_path, &token)?;
            println!("Authentication successful!\n");
            return Ok(token);
        }

        if let Ok(err) = serde_json::from_str::<serde_json::Value>(&body) {
            if let Some(error) = err.get("error").and_then(|v| v.as_str()) {
                if error == "authorization_pending" {
                    tracing::debug!("Authorization pending...");
                    continue;
                }
                bail!("Authentication error: {} - {}", error, err.get("error_description").and_then(|v| v.as_str()).unwrap_or("Unknown"));
            }
        }

        bail!("Unexpected token response: {}", body);
    }
}

async fn refresh_token(client_id: &str, refresh_token: &str, cache_path: &Path) -> Result<TokenResponse> {
    let client = Client::new();
    let params = [
        ("grant_type", "refresh_token"),
        ("client_id", client_id),
        ("refresh_token", refresh_token),
        ("scope", SCOPE),
    ];

    let res = client
        .post(TOKEN_URL)
        .form(&params)
        .send()
        .await
        .context("Failed to refresh token")?;

    let status = res.status();
    let body = res.text().await.context("Failed to read refresh response body")?;

    if !status.is_success() {
        if body.contains("invalid_grant") {
            tracing::warn!("Refresh token invalid or expired, re-authenticating...");
            return device_code_flow(client_id, cache_path).await;
        }
        bail!("Token refresh failed: {}", body);
    }

    let mut token: TokenResponse = serde_json::from_str(&body)
        .with_context(|| format!("Failed to parse refresh response: {}", body))?;
    token.obtained_at = Some(SystemTime::now());
    save_cached_token(cache_path, &token)?;
    Ok(token)
}

fn load_cached_token(path: &Path) -> Result<CachedToken> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read token cache from {}", path.display()))?;
    let token: CachedToken = serde_json::from_str(&content)
        .context("Failed to parse token cache")?;
    Ok(token)
}

fn save_cached_token(path: &Path, token: &TokenResponse) -> Result<()> {
    let expires_at = chrono::Utc::now().timestamp() + token.expires_in as i64;
    let cached = CachedToken {
        access_token: token.access_token.clone(),
        refresh_token: token.refresh_token.clone(),
        expires_at,
    };
    let content = serde_json::to_string_pretty(&cached)
        .context("Failed to serialize token cache")?;
    std::fs::write(path, content)
        .with_context(|| format!("Failed to write token cache to {}", path.display()))?;
    Ok(())
}
