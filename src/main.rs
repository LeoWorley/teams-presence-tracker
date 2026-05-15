use std::path::PathBuf;
use anyhow::Result;
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;

mod auth;
mod config;
mod db;
mod graph;
mod server;
mod tracker;

use config::Config;
use db::{export_csv, get_history};

#[derive(Parser)]
#[command(name = "teams-presence-tracker")]
#[command(about = "Track Microsoft Teams presence history via Graph API")]
struct Cli {
    /// Microsoft Azure AD application client ID
    #[arg(long, env = "TEAMS_CLIENT_ID")]
    client_id: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Parser)]
struct GlobalRunArgs {
    /// Polling interval in seconds
    #[arg(short, long, default_value = "60")]
    interval: u64,
    /// Comma-separated list of users to track (email or object ID).
    /// If omitted, tracks your own presence.
    #[arg(short, long, value_delimiter = ',')]
    users: Vec<String>,
    /// Start a local HTTP dashboard on the given address (e.g. 0.0.0.0:8080)
    #[arg(long)]
    serve: Option<String>,
    /// Fail instead of prompting for interactive authentication.
    /// Use this when running as a scheduled task or service.
    #[arg(long)]
    no_interactive: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Start polling Teams presence and recording changes
    Run(GlobalRunArgs),
    /// Show presence history from the database
    History {
        /// Number of records to show
        #[arg(short, long, default_value = "50")]
        limit: usize,
    },
    /// Export presence history to CSV
    Export {
        /// Output file path
        #[arg(short, long, default_value = "presence_history.csv")]
        output: PathBuf,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let cli = Cli::parse();
    let config = Config::new(cli.client_id)?;

    match cli.command {
        Commands::Run(args) => {
            println!("Starting Teams presence tracker...");
            println!("Data directory: {}", config.data_dir.display());
            println!("Database: {}", config.db_path().display());
            if !args.users.is_empty() {
                println!("Tracking users: {}", args.users.join(", "));
            } else {
                println!("Tracking: your own presence");
            }

            if let Some(addr) = &args.serve {
                let db = config.db_path();
                let addr = addr.clone();
                tokio::spawn(async move {
                    if let Err(e) = server::serve(&addr, db).await {
                        tracing::error!("Server error: {}", e);
                    }
                });
            }

            println!("Press Ctrl+C to stop\n");
            tracker::run_tracker(&config, args.interval, args.users, args.no_interactive).await?;
        }
        Commands::History { limit } => {
            let db_path = config.db_path();
            let records = get_history(&db_path, limit)?;

            if records.is_empty() {
                println!("No presence history found.");
                return Ok(());
            }

            println!("{:<5} {:<20} {:<15} {:<20} {:<30} {}",
                "ID", "Recorded At", "Availability", "Activity", "Status Message", "User ID");
            println!("{}", "-".repeat(120));
            for r in records {
                println!("{:<5} {:<20} {:<15} {:<20} {:<30} {}",
                    r.id,
                    r.recorded_at.format("%Y-%m-%d %H:%M:%S"),
                    r.availability,
                    r.activity,
                    r.status_message.chars().take(28).collect::<String>(),
                    r.user_id
                );
            }
        }
        Commands::Export { output } => {
            let db_path = config.db_path();
            export_csv(&db_path, &output)?;
            println!("Exported history to {}", output.display());
        }
    }

    Ok(())
}
