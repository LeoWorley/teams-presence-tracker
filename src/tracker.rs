use std::collections::HashMap;
use std::path::Path;
use std::time::Duration;
use anyhow::Result;
use tokio::time::sleep;

use crate::auth::{authenticate, TokenResponse};
use crate::config::Config;
use crate::db::record_presence;
use crate::graph::{get_presence, Presence};

pub async fn run_tracker(config: &Config, interval_secs: u64, users: Vec<String>, no_interactive: bool) -> Result<()> {
    let db_path = config.db_path();

    println!("Authenticating with Microsoft Graph...");
    let mut token = authenticate(&config.client_id, &config.token_cache_path(), no_interactive).await?;

    let target_users = if users.is_empty() {
        vec!["me".to_string()]
    } else {
        users
    };

    println!("Fetching initial presence for {} user(s)...", target_users.len());
    let mut last_states: HashMap<String, Presence> = HashMap::new();

    for user in &target_users {
        match fetch_and_record(&token, &db_path, user).await {
            Ok(presence) => {
                print_presence_change(user, &presence, None);
                last_states.insert(user.clone(), presence);
            }
            Err(e) => {
                tracing::error!("Failed to fetch presence for {}: {}", user, e);
            }
        }
    }

    let interval = Duration::from_secs(interval_secs);

    loop {
        sleep(interval).await;

        if token.is_expired() {
            tracing::info!("Access token expired, refreshing...");
            token = authenticate(&config.client_id, &config.token_cache_path(), no_interactive).await?;
        }

        for user in &target_users {
            match fetch_and_record(&token, &db_path, user).await {
                Ok(presence) => {
                    let changed = match last_states.get(user) {
                        Some(last) => has_changed(&presence, last),
                        None => true,
                    };
                    if changed {
                        print_presence_change(user, &presence, last_states.get(user));
                        last_states.insert(user.clone(), presence);
                    }
                }
                Err(e) => {
                    tracing::error!("Failed to fetch presence for {}: {}", user, e);
                }
            }
        }
    }
}

async fn fetch_and_record(token: &TokenResponse, db_path: &Path, user: &str) -> Result<Presence> {
    let user_id = if user == "me" { None } else { Some(user) };
    let presence = get_presence(&token.access_token, user_id).await?;
    let record_user_id = presence.id.clone().unwrap_or_else(|| user.to_string());
    record_presence(db_path, &record_user_id, &presence)?;
    Ok(presence)
}

fn has_changed(a: &Presence, b: &Presence) -> bool {
    a.availability() != b.availability()
        || a.activity() != b.activity()
        || a.status_text() != b.status_text()
}

fn print_presence_change(user: &str, presence: &Presence, previous: Option<&Presence>) {
    let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
    let user_label = if user == "me" { "You" } else { user };
    if let Some(prev) = previous {
        println!(
            "[{}] {} changed: {} / {} → {} / {}",
            now,
            user_label,
            prev.availability(),
            prev.activity(),
            presence.availability(),
            presence.activity()
        );
    } else {
        println!(
            "[{}] {} status: {} / {}",
            now,
            user_label,
            presence.availability(),
            presence.activity()
        );
    }
    if !presence.status_text().is_empty() {
        println!("  Status message: {}", presence.status_text());
    }
}
