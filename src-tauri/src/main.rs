#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, PhysicalPosition, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

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
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(BackendState::default())
        .setup(|app| {
            // ── 1. Start / attach backend ─────────────────────────────────────
            {
                let state = app.state::<BackendState>();
                let child = ensure_backend().map_err(std::io::Error::other)?;
                if let Ok(mut guard) = state.child.lock() {
                    *guard = child;
                };
            }

            // ── 2. Show main Odysseus window ──────────────────────────────────
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
            }

            // ── 3. Position Jarvis widget in the bottom-right corner ──────────
            if let Some(widget) = app.get_webview_window("jarvis-widget") {
                // Try to snap to bottom-right, above the Windows taskbar (~48 px).
                if let Ok(Some(monitor)) = widget.primary_monitor() {
                    let screen = monitor.size();
                    let scale = monitor.scale_factor();
                    let w_logical = 380.0_f64;
                    let h_logical = 560.0_f64;
                    let w_px = (w_logical * scale) as i32;
                    let h_px = (h_logical * scale) as i32;
                    let margin_right = (20.0 * scale) as i32;
                    let margin_bottom = (60.0 * scale) as i32; // above taskbar
                    let x = screen.width as i32 - w_px - margin_right;
                    let y = screen.height as i32 - h_px - margin_bottom;
                    let _ = widget.set_position(PhysicalPosition::new(x, y));
                }

                // Hide instead of closing so the widget persists in the tray.
                let widget_hide = widget.clone();
                widget.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = widget_hide.hide();
                    }
                });
            }

            // ── 4. Global hotkey: Ctrl+Alt+Space → toggle widget ─────────────
            let hotkey =
                Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space);
            let app_handle = app.handle().clone();
            if let Err(e) = app.global_shortcut().on_shortcut(
                hotkey,
                move |_app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        if let Some(w) = app_handle.get_webview_window("jarvis-widget") {
                            if w.is_visible().unwrap_or(false) {
                                let _ = w.hide();
                            } else {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                    }
                },
            ) {
                eprintln!(
                    "[Jarvis] Could not register Ctrl+Alt+Space hotkey: {}. \
                     Use the tray icon to show/hide instead.",
                    e
                );
            }

            // ── 5. System tray ────────────────────────────────────────────────
            let show_item =
                MenuItem::with_id(app, "show_widget", "Show Jarvis", true, None::<&str>)?;
            let hide_item =
                MenuItem::with_id(app, "hide_widget", "Hide Jarvis", true, None::<&str>)?;
            let open_main =
                MenuItem::with_id(app, "open_main", "Open Full App", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(app)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Jarvis", true, None::<&str>)?;

            let menu = Menu::with_items(
                app,
                &[&show_item, &hide_item, &open_main, &sep, &quit_item],
            )?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("Jarvis — Ctrl+Alt+Space to toggle")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show_widget" => {
                        if let Some(w) = app.get_webview_window("jarvis-widget") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "hide_widget" => {
                        if let Some(w) = app.get_webview_window("jarvis-widget") {
                            let _ = w.hide();
                        }
                    }
                    "open_main" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Left-click the tray icon → toggle widget visibility.
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("jarvis-widget") {
                            if w.is_visible().unwrap_or(false) {
                                let _ = w.hide();
                            } else {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Jarvis desktop shell")
        .run(|app_handle, event| {
            if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
                let state = app_handle.state::<BackendState>();
                stop_backend(&state);
            }
        });
}
