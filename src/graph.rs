use anyhow::{bail, Context, Result};
use reqwest::Client;
use serde::Deserialize;

#[derive(Debug, Deserialize, Clone)]
pub struct Presence {
    #[serde(rename = "@odata.context")]
    pub _odata_context: Option<String>,
    pub id: Option<String>,
    pub availability: Option<String>,
    pub activity: Option<String>,
    pub status_message: Option<StatusMessage>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct StatusMessage {
    pub message: Option<StatusMessageContent>,
    pub expiry_date_time: Option<DateTimeWrapper>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct StatusMessageContent {
    pub content: Option<String>,
    pub content_type: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct DateTimeWrapper {
    pub date_time: Option<String>,
    pub time_zone: Option<String>,
}

impl Presence {
    pub fn availability(&self) -> &str {
        self.availability.as_deref().unwrap_or("Unknown")
    }

    pub fn activity(&self) -> &str {
        self.activity.as_deref().unwrap_or("Unknown")
    }

    pub fn status_text(&self) -> &str {
        self.status_message
            .as_ref()
            .and_then(|sm| sm.message.as_ref())
            .and_then(|m| m.content.as_deref())
            .unwrap_or("")
    }
}

pub async fn get_presence(access_token: &str) -> Result<Presence> {
    let client = Client::new();
    let res = client
        .get("https://graph.microsoft.com/v1.0/me/presence")
        .header("Authorization", format!("Bearer {}", access_token))
        .send()
        .await
        .context("Failed to send presence request")?;

    let status = res.status();
    let body = res.text().await.context("Failed to read presence response body")?;

    if !status.is_success() {
        bail!("Graph API error ({}): {}", status, body);
    }

    let presence: Presence = serde_json::from_str(&body)
        .with_context(|| format!("Failed to parse presence response: {}", body))?;

    Ok(presence)
}
