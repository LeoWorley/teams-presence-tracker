use std::path::Path;
use std::time::Duration;
use anyhow::Result;
use tokio::time::sleep;

use crate::auth::{authenticate, TokenResponse};
use crate::config::Config;
use crate::db::record_presence;
use crate::graph::{get_presence, Presence};

pub async fn run_tracker(config: &Config, interval_secs: u64) -> Result<()> {
    let db_path = config.db_path();

    println!("Authenticating with Microsoft Graph...");
    let mut token = authenticate(&config.client_id, &config.token_cache_path()).await?;

    println!("Fetching initial presence...");
    let mut last_presence = fetch_and_record(&token, &db_path).await?;
    print_presence_change(&last_presence, None);

    let interval = Duration::from_secs(interval_secs);

    loop {
        sleep(interval).await;

        if token.is_expired() {
            tracing::info!("Access token expired, refreshing...");
            token = authenticate(&config.client_id, &config.token_cache_path()).await?;
        }

        match fetch_and_record(&token, &db_path).await {
            Ok(presence) => {
                if has_changed(&presence, &last_presence) {
                    print_presence_change(&presence, Some(&last_presence));
                    last_presence = presence;
                }
            }
            Err(e) => {
                tracing::error!("Failed to fetch presence: {}", e);
            }
        }
    }
}

async fn fetch_and_record(token: &TokenResponse, db_path: &Path) -> Result<Presence> {
    let presence = get_presence(&token.access_token).await?;
    let user_id = presence.id.clone().unwrap_or_else(|| "me".to_string());
    record_presence(db_path, &user_id, &presence)?;
    Ok(presence)
}

fn has_changed(a: &Presence, b: &Presence) -> bool {
    a.availability() != b.availability()
        || a.activity() != b.activity()
        || a.status_text() != b.status_text()
}

fn print_presence_change(presence: &Presence, previous: Option<&Presence>) {
    let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
    if let Some(prev) = previous {
        println!(
            "[{}] Status changed: {} / {} → {} / {}",
            now,
            prev.availability(),
            prev.activity(),
            presence.availability(),
            presence.activity()
        );
    } else {
        println!(
            "[{}] Current status: {} / {}",
            now,
            presence.availability(),
            presence.activity()
        );
    }
    if !presence.status_text().is_empty() {
        println!("  Status message: {}", presence.status_text());
    }
}
