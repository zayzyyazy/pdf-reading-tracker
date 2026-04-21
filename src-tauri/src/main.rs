#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::webview::WebviewWindowBuilder;
use tauri::{AppHandle, Manager, RunEvent, State, Url, WebviewUrl};

struct ServerChild(Mutex<Option<Child>>);

fn workspace_root(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .canonicalize()
            .map_err(|e| e.to_string())
    } else {
        let res = app.path().resource_dir().map_err(|e| e.to_string())?;
        // Tauri bundles `../app` paths under `Resources/_up_/`.
        let nested = res.join("_up_");
        if nested.join("app").is_dir() {
            nested.canonicalize().map_err(|e| e.to_string())
        } else {
            Ok(res)
        }
    }
}

fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|p| p.join("workspace-data"))
        .map_err(|e| e.to_string())
}

fn pick_port() -> u16 {
    for p in 18432u16..19100u16 {
        if std::net::TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return p;
        }
    }
    18432
}

fn wait_for_port(port: u16) -> bool {
    for _ in 0..300 {
        if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
            thread::sleep(Duration::from_millis(120));
            return true;
        }
        thread::sleep(Duration::from_millis(40));
    }
    false
}

fn spawn_server(root: &PathBuf, data: &PathBuf, port: u16) -> Result<Child, String> {
    std::fs::create_dir_all(data).map_err(|e| e.to_string())?;
    let sqlite = data.join("research.sqlite");
    let uploads = data.join("workspace_uploads");
    std::fs::create_dir_all(&uploads).map_err(|e| e.to_string())?;

    let python = std::env::var("RESEARCH_WORKSPACE_PYTHON").unwrap_or_else(|_| "python3".into());

    Command::new(&python)
        .current_dir(root)
        .env("PYTHONPATH", root.as_os_str())
        .env("PYTHONUNBUFFERED", "1")
        .env("RESEARCH_WORKSPACE_ROOT", root.as_os_str())
        .env("RESEARCH_SQLITE_PATH", sqlite.as_os_str())
        .env("RESEARCH_UPLOADS_DIR", uploads.as_os_str())
        .args([
            "-m",
            "uvicorn",
            "app.workspace_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| {
            format!(
                "Could not start Python ({python}): {e}\n\nFrom the project folder run:\n  python3 -m pip install -r requirements.txt"
            )
        })
}

fn kill_server(state: &ServerChild) {
    if let Ok(mut g) = state.0.lock() {
        if let Some(mut child) = g.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(ServerChild(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let root = workspace_root(&handle)?;
            let data = data_dir(&handle)?;
            let port = pick_port();

            let child = spawn_server(&root, &data, port)?;
            {
                let state: State<ServerChild> = app.state();
                *state.0.lock().map_err(|_| "server state lock poisoned")? = Some(child);
            }

            if !wait_for_port(port) {
                let state: State<ServerChild> = app.state();
                kill_server(&state);
                return Err("The workspace server did not become ready in time.".into());
            }

            let url: Url = Url::parse(&format!("http://127.0.0.1:{port}/"))
                .map_err(|e| format!("Invalid server URL: {e}"))?;

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Research Workspace")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 640.0)
                .center()
                .build()
                .map_err(|e| e.to_string())?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit) {
                if let Some(state) = app_handle.try_state::<ServerChild>() {
                    kill_server(&*state);
                }
            }
        });
}
