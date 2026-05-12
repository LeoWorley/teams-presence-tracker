use std::collections::HashMap;
use std::path::Path;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

use crate::db::get_history;

pub async fn serve(addr: &str, db_path: std::path::PathBuf) -> anyhow::Result<()> {
    let listener = TcpListener::bind(addr).await?;
    println!("📱 Dashboard running at http://{}", addr);
    println!("   Open that URL on your phone browser (same WiFi)\n");

    loop {
        let (mut stream, _) = listener.accept().await?;
        let path = db_path.clone();

        tokio::spawn(async move {
            let mut buf = [0u8; 1024];
            let _ = stream.read(&mut buf).await;
            let request = String::from_utf8_lossy(&buf);

            let response = if request.starts_with("GET / ") || request.starts_with("GET / HTTP")
            {
                build_dashboard(&path).await
            } else {
                "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n".to_string()
            };

            let _ = stream.write_all(response.as_bytes()).await;
        });
    }
}

async fn build_dashboard(db_path: &Path) -> String {
    let records = get_history(db_path, 1000).unwrap_or_default();

    // Most recent record per user
    let mut current: HashMap<String, (String, String, String)> = HashMap::new();
    for r in records {
        if !current.contains_key(&r.user_id) {
            current.insert(
                r.user_id.clone(),
                (
                    r.availability,
                    r.activity,
                    r.recorded_at.format("%Y-%m-%d %H:%M:%S").to_string(),
                ),
            );
        }
    }

    let mut rows = String::new();
    for (user, (avail, activity, time)) in current {
        let color = status_color(&avail);
        rows.push_str(&format!(
            r#"<tr><td>{}</td><td><span class="badge" style="background:{};color:#fff">{}</span></td><td>{}</td><td>{}</td></tr>"#,
            html_escape(&user),
            color,
            avail,
            activity,
            time
        ));
    }

    if rows.is_empty() {
        rows.push_str(r#"<tr><td colspan="4" style="text-align:center;color:#666">No data yet. Start the tracker and check back.</td></tr>"#);
    }

    let html = format!(
        r#"<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teams Presence</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;margin:16px;background:#f0f2f5}}
h1{{font-size:1.4rem;color:#1a1a1a;margin-bottom:8px}}
.sub{{color:#666;font-size:0.85rem;margin-bottom:16px}}
table{{width:100%;background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.1);border-collapse:collapse;overflow:hidden}}
th,td{{padding:14px 12px;text-align:left;border-bottom:1px solid #eee}}
th{{background:#2b2b2b;color:#fff;font-weight:600;font-size:0.85rem;text-transform:uppercase}}
tr:last-child td{{border-bottom:none}}
.badge{{padding:5px 14px;border-radius:20px;font-size:0.8rem;font-weight:700;display:inline-block}}
.footer{{color:#888;font-size:0.75rem;margin-top:12px;text-align:center}}
</style>
<meta http-equiv="refresh" content="15">
</head>
<body>
<h1>👥 Teams Presence</h1>
<p class="sub">Live status dashboard &middot; Refreshes every 15s</p>
<table>
<tr><th>User</th><th>Availability</th><th>Activity</th><th>Updated</th></tr>
{}
</table>
<p class="footer">Auto-refreshes every 15 seconds</p>
</body>
</html>"#,
        rows
    );

    format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        html.len(),
        html
    )
}

fn status_color(avail: &str) -> &str {
    match avail.to_lowercase().as_str() {
        "available" | "free" => "#28a745",
        "busy" | "donotdisturb" | "inacall" | "inaconferencecall" => "#dc3545",
        "away" | "berightback" => "#ffc107",
        "offline" | "presenceunknown" => "#6c757d",
        _ => "#17a2b8",
    }
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
