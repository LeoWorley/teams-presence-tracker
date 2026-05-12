use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader};
use std::path::Path;
use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use crate::graph::Presence;

#[derive(Debug)]
pub struct PresenceRecord {
    pub id: usize,
    pub user_id: String,
    pub display_name: Option<String>,
    pub availability: String,
    pub activity: String,
    pub status_message: String,
    pub recorded_at: DateTime<Utc>,
}

fn ensure_csv_headers(path: &Path) -> Result<()> {
    if !path.exists() {
        let mut writer = csv::Writer::from_path(path)
            .with_context(|| format!("Failed to create CSV at {}", path.display()))?;
        writer.write_record(["id", "user_id", "display_name", "availability", "activity", "status_message", "recorded_at"])?;
        writer.flush()?;
    }
    Ok(())
}

pub fn record_presence(path: &Path, user_id: &str, presence: &Presence) -> Result<()> {
    ensure_csv_headers(path)?;

    let file = OpenOptions::new()
        .append(true)
        .open(path)
        .with_context(|| format!("Failed to open CSV for append: {}", path.display()))?;

    let mut writer = csv::Writer::from_writer(file);
    let now = Utc::now().to_rfc3339();
    writer.write_record([
        "", // id placeholder; we'll assign on read
        user_id,
        presence.id.as_deref().unwrap_or(""),
        presence.availability(),
        presence.activity(),
        presence.status_text(),
        &now,
    ])?;
    writer.flush()?;
    Ok(())
}

pub fn get_history(path: &Path, limit: usize) -> Result<Vec<PresenceRecord>> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    let file = File::open(path)
        .with_context(|| format!("Failed to open CSV: {}", path.display()))?;
    let reader = BufReader::new(file);
    let mut lines = reader.lines().collect::<Result<Vec<_>, _>>()
        .context("Failed to read CSV lines")?;

    // Drop header
    if !lines.is_empty() {
        lines.remove(0);
    }

    let mut records = Vec::with_capacity(lines.len());
    for (idx, line) in lines.iter().enumerate() {
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() >= 7 {
            records.push(PresenceRecord {
                id: idx + 1,
                user_id: cols[1].to_string(),
                display_name: if cols[2].is_empty() { None } else { Some(cols[2].to_string()) },
                availability: cols[3].to_string(),
                activity: cols[4].to_string(),
                status_message: cols[5].to_string(),
                recorded_at: cols[6].parse().unwrap_or_else(|_| Utc::now()),
            });
        }
    }

    // Return most recent first, limited
    records.reverse();
    records.truncate(limit);
    Ok(records)
}

pub fn export_csv(source: &Path, output: &Path) -> Result<()> {
    std::fs::copy(source, output)
        .with_context(|| format!("Failed to copy {} to {}", source.display(), output.display()))?;
    Ok(())
}
