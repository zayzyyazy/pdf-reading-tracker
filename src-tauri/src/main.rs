#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use tauri::webview::WebviewWindowBuilder;
use tauri::{Manager, RunEvent, State, Url, WebviewUrl};

/// Matches `identifier` in `tauri.conf.json`.
const APP_SUPPORT_LEAF: &str = "com.research.workspace/workspace-data";

struct LauncherInner {
    port: u16,
    child: Mutex<Option<Child>>,
}

fn show_startup_error(title: &str, message: &str) {
    eprintln!("{title}: {message}");
    #[cfg(target_os = "macos")]
    {
        let msg: String = message.chars().take(900).collect::<String>().replace('"', "'");
        let t: String = title.chars().take(120).collect::<String>().replace('"', "'");
        let script = format!(
            r#"display dialog "{msg}" with title "{t}" buttons {{"OK"}} default button "OK" with icon stop"#
        );
        let _ = Command::new("osascript").args(["-e", &script]).status();
    }
}

fn kill_child(inner: &Arc<LauncherInner>) {
    if let Ok(mut g) = inner.child.lock() {
        if let Some(mut child) = g.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn resolve_workspace_root() -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .canonicalize()
            .map_err(|e| format!("Could not resolve project root: {e}"));
    }

    let exe = std::env::current_exe().map_err(|e| format!("Could not read executable path: {e}"))?;
    let dir = exe
        .parent()
        .ok_or_else(|| "Executable has no parent directory".to_string())?
        .to_path_buf();

    if dir.ends_with("MacOS") {
        let resources = dir
            .parent()
            .ok_or_else(|| "Invalid .app bundle layout".to_string())?
            .join("Resources");
        let nested = resources.join("_up_");
        if nested.join("app").is_dir() {
            return Ok(match nested.canonicalize() {
                Ok(p) => p,
                Err(_) => nested,
            });
        }
        if resources.join("app").is_dir() {
            return Ok(match resources.canonicalize() {
                Ok(p) => p,
                Err(_) => resources,
            });
        }
        return Err(format!(
            "Bundled app not found. Expected app/ under:\n{}\nor\n{}",
            nested.display(),
            resources.display()
        ));
    }

    Err(
        "Could not locate bundled workspace (expected a macOS .app).".to_string(),
    )
}

fn resolve_data_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|_| "HOME is not set.".to_string())?;
    let p = PathBuf::from(home)
        .join("Library/Application Support")
        .join(APP_SUPPORT_LEAF);
    std::fs::create_dir_all(&p).map_err(|e| format!("Could not create data directory {}: {e}", p.display()))?;
    Ok(p)
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

fn read_stderr_head(child: &mut Child, max: usize) -> String {
    let mut out = String::new();
    if let Some(mut stderr) = child.stderr.take() {
        let mut buf = vec![0u8; max];
        if let Ok(n) = stderr.read(&mut buf) {
            out.push_str(&String::from_utf8_lossy(&buf[..n]));
        }
    }
    out.trim().to_string()
}

fn is_executable_file(path: &PathBuf) -> bool {
    std::fs::metadata(path).map(|m| m.is_file()).unwrap_or(false)
}

fn resolve_python_interpreter(root: &PathBuf) -> Result<PathBuf, String> {
    if let Ok(explicit) = std::env::var("RESEARCH_WORKSPACE_PYTHON") {
        let p = PathBuf::from(explicit);
        if is_executable_file(&p) {
            return Ok(p);
        }
        return Err(format!(
            "RESEARCH_WORKSPACE_PYTHON is set, but not executable: {}",
            p.display()
        ));
    }

    let candidates = [
        root.join(".venv/bin/python3"),
        root.join(".venv/bin/python"),
    ];
    for candidate in candidates {
        if is_executable_file(&candidate) {
            return Ok(candidate);
        }
    }

    Err(format!(
        "Could not find a project Python interpreter.\nExpected one of:\n  {}\n  {}\n\nCreate the project venv and install dependencies before launching the desktop app.",
        root.join(".venv/bin/python3").display(),
        root.join(".venv/bin/python").display()
    ))
}

fn spawn_server(root: &PathBuf, data: &PathBuf, port: u16) -> Result<Child, String> {
    let sqlite = data.join("research.sqlite");
    let uploads = data.join("workspace_uploads");
    std::fs::create_dir_all(&uploads).map_err(|e| format!("Could not create uploads dir: {e}"))?;

    let python = resolve_python_interpreter(root)?;
    eprintln!(
        "Research Workspace launcher using Python: {}",
        python.display()
    );

    let mut child = Command::new(&python)
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
                "Could not start `{}`.\nInstall dependencies in the project .venv and try again.\n\nDetails: {e}",
                python.display()
            )
        })?;

    if !wait_for_port(port) {
        let err = read_stderr_head(&mut child, 4096);
        let _ = child.kill();
        let _ = child.wait();
        let hint = if err.is_empty() {
            "The server never listened on the port (missing uvicorn/FastAPI?).".to_string()
        } else {
            format!("Server output:\n{err}")
        };
        return Err(hint);
    }

    Ok(child)
}

fn start_backend() -> Result<(Child, u16), String> {
    let root = resolve_workspace_root()?;
    let data = resolve_data_dir()?;
    let port = pick_port();
    let child = spawn_server(&root, &data, port)?;
    Ok((child, port))
}

fn main() {
    let (child, port) = match start_backend() {
        Ok(x) => x,
        Err(e) => {
            show_startup_error("Research Workspace", &e);
            std::process::exit(1);
        }
    };

    let inner = Arc::new(LauncherInner {
        port,
        child: Mutex::new(Some(child)),
    });
    let inner_on_build_fail = Arc::clone(&inner);

    let app = match tauri::Builder::default()
        .manage(Arc::clone(&inner))
        .setup(move |app| {
            let launcher: State<Arc<LauncherInner>> = app.state();
            let url = match Url::parse(&format!("http://127.0.0.1:{}/", launcher.port)) {
                Ok(u) => u,
                Err(e) => {
                    show_startup_error(
                        "Research Workspace",
                        &format!("Internal URL error: {e}"),
                    );
                    kill_child(launcher.inner());
                    std::process::exit(1);
                }
            };

            if let Err(e) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Research Workspace")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 640.0)
                .center()
                .build()
            {
                let msg = format!("Could not open the workspace window: {e}");
                show_startup_error("Research Workspace", &msg);
                kill_child(launcher.inner());
                std::process::exit(1);
            }

            Ok(())
        })
        .build(tauri::generate_context!())
    {
        Ok(a) => a,
        Err(e) => {
            show_startup_error(
                "Research Workspace",
                &format!("Desktop shell failed to initialize: {e}"),
            );
            kill_child(&inner_on_build_fail);
            std::process::exit(1);
        }
    };

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(launcher) = app_handle.try_state::<Arc<LauncherInner>>() {
                kill_child(launcher.inner());
            }
        }
    });
}
