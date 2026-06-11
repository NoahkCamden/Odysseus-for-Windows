#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::Manager;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "7000";

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<Child>>,
}

fn has_app_entrypoint(path: &PathBuf) -> bool {
    path.join("app.py").exists()
}

fn find_project_root_from(start: &PathBuf) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|candidate| candidate.join("app.py").exists())
        .map(|candidate| candidate.to_path_buf())
}

fn app_root_dir() -> PathBuf {
    if let Ok(from_env) = std::env::var("ODYSSEUS_APP_DIR") {
        let candidate = PathBuf::from(from_env);
        if has_app_entrypoint(&candidate) {
            return candidate;
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        if has_app_entrypoint(&cwd) {
            return cwd;
        }
        if let Some(found) = find_project_root_from(&cwd) {
            return found;
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(found) = find_project_root_from(&exe) {
            return found;
        }
    }

    PathBuf::from(".")
}

fn backend_candidates(app_dir: &PathBuf) -> Vec<(String, Vec<String>)> {
    let mut candidates: Vec<(String, Vec<String>)> = Vec::new();
    if let Ok(cmd) = std::env::var("ODYSSEUS_PYTHON") {
        candidates.push((
            cmd,
            vec![
                "-m".into(),
                "uvicorn".into(),
                "app:app".into(),
                "--host".into(),
                BACKEND_HOST.into(),
                "--port".into(),
                BACKEND_PORT.into(),
            ],
        ));
    }

    let local_paths = [
        app_dir.join(".venv").join("Scripts").join("python.exe"),
        app_dir.join("venv").join("Scripts").join("python.exe"),
        app_dir.join(".venv").join("bin").join("python"),
        app_dir.join("venv").join("bin").join("python"),
    ];

    for path in local_paths {
        if path.exists() {
            candidates.push((
                path.to_string_lossy().to_string(),
                vec![
                    "-m".into(),
                    "uvicorn".into(),
                    "app:app".into(),
                    "--host".into(),
                    BACKEND_HOST.into(),
                    "--port".into(),
                    BACKEND_PORT.into(),
                ],
            ));
        }
    }

    candidates.push((
        "python".into(),
        vec![
            "-m".into(),
            "uvicorn".into(),
            "app:app".into(),
            "--host".into(),
            BACKEND_HOST.into(),
            "--port".into(),
            BACKEND_PORT.into(),
        ],
    ));
    candidates.push((
        "python3".into(),
        vec![
            "-m".into(),
            "uvicorn".into(),
            "app:app".into(),
            "--host".into(),
            BACKEND_HOST.into(),
            "--port".into(),
            BACKEND_PORT.into(),
        ],
    ));
    #[cfg(target_os = "windows")]
    candidates.push((
        "py".into(),
        vec![
            "-3".into(),
            "-m".into(),
            "uvicorn".into(),
            "app:app".into(),
            "--host".into(),
            BACKEND_HOST.into(),
            "--port".into(),
            BACKEND_PORT.into(),
        ],
    ));

    candidates
}

fn wait_for_backend(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect((BACKEND_HOST, BACKEND_PORT.parse::<u16>().unwrap_or(7000))).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn start_backend() -> Result<Child, String> {
    let cwd = app_root_dir();
    let mut errors: Vec<String> = Vec::new();
    for (command, args) in backend_candidates(&cwd) {
        let mut child = match Command::new(&command)
            .args(&args)
            .current_dir(&cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(process) => process,
            Err(e) => {
                errors.push(format!("{} spawn failed: {}", command, e));
                continue;
            }
        };

        thread::sleep(Duration::from_millis(300));
        match child.try_wait() {
            Ok(Some(status)) => {
                errors.push(format!("{} exited early with {}", command, status));
                continue;
            }
            Ok(None) => {
                if wait_for_backend(Duration::from_secs(20)) {
                    return Ok(child);
                }
                let _ = child.kill();
                let _ = child.wait();
                errors.push(format!("{} started but backend did not become ready", command));
            }
            Err(e) => {
                let _ = child.kill();
                let _ = child.wait();
                errors.push(format!("{} status check failed: {}", command, e));
            }
        }
    }

    Err(format!(
        "Failed to start Odysseus backend. Checked commands in {:?}. Errors: {}",
        cwd,
        errors.join(" | ")
    ))
}

fn ensure_backend() -> Result<Option<Child>, String> {
    if wait_for_backend(Duration::from_millis(500)) {
        return Ok(None);
    }
    start_backend().map(Some)
}

fn stop_backend(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .setup(|app| {
            {
                let state = app.state::<BackendState>();
                let child = ensure_backend().map_err(std::io::Error::other)?;
                if let Ok(mut guard) = state.child.lock() {
                    *guard = child;
                };
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Odysseus desktop shell")
        .run(|app_handle, event| {
            if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
                let state = app_handle.state::<BackendState>();
                stop_backend(&state);
            }
        });
}
