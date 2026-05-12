use std::path::PathBuf;
use anyhow::{Context, Result};

pub struct Config {
    pub client_id: String,
    pub data_dir: PathBuf,
}

impl Config {
    pub fn new(client_id: String) -> Result<Self> {
        let data_dir = dirs::data_dir()
            .context("Could not determine user data directory")?
            .join("teams-presence-tracker");
        
        std::fs::create_dir_all(&data_dir)
            .with_context(|| format!("Failed to create data directory: {}", data_dir.display()))?;

        Ok(Self { client_id, data_dir })
    }

    pub fn token_cache_path(&self) -> PathBuf {
        self.data_dir.join("token_cache.json")
    }

    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("presence_history.db")
    }
}
