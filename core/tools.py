import pyautogui
import json
import sys
import os
import glob
import subprocess
import winreg
import win32com.client
import screen_brightness_control as sbc
from pycaw.pycaw import AudioUtilities
from comtypes import CoInitialize, CoUninitialize
from langchain.tools import tool
import ctypes
import win32gui
import win32con
import time
import psutil
import pygetwindow  as gw
from file_manager import write_docx
import wikipediaapi
import requests
from tavily import TavilyClient
import win32api

from collections import defaultdict



import shutil as _shutil
import numpy as _np
from file_manager import (
    read_file_content,
    index_file,
    remove_file_from_index,
    _get_conn as _fm_get_conn,
    embedder as _fm_embedder,
    cosine_similarity as _cosine_sim,
    write_docx,
    get_file_type
)



import subprocess
import re
import shlex

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "profiles")
WATCHED_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "agent_workspace")


_DEVMODE_CACHE = {}  # (width, height, refresh) -> raw enum index

BLOCKED_PATTERNS = [
    r"\bdel\b", r"\brd\b", r"\brmdir\b",
    r"\bformat\b",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\b",
    r"\bbcdedit\b",
    r"\bdiskpart\b",
    r"\bsfc\b", r"\bdism\b",
    r"\bpowershell\s+-exec\s+bypass\b",
    r"curl.*\|\s*(bash|cmd|powershell)",
    r"\bicacls\b", r"\bcacls\b",
    r"\bsc\s+delete\b",
    r"\battrib\s+.*\+s\b",  # system file attribute changes
    r"\bmklink\b",           # symlink creation
    r">>\s*\S+\.(bat|cmd|ps1)\b",  # writing to script files
]

REQUIRES_CONFIRMATION = [
    r"\bshutdown\b",
    r"\brestart\b",
    r"\btaskkill\b",
    r"\bwinget\s+(install|uninstall)\b",
    r"\bnetsh\b",
    r"\bnet\s+stop\b",
]

def _is_safe(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower().strip()
    
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower):
            return False, f"Command blocked — contains restricted operation: '{pattern}'"
    
    return True, ""

def _needs_confirmation(command: str) -> bool:
    cmd_lower = command.lower().strip()
    for pattern in REQUIRES_CONFIRMATION:
        if re.search(pattern, cmd_lower):
            return True
    return False

def _run_command(command: str, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            # critical — don't inherit elevated privileges
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        
        if error and not output:
            return f"Error: {error}"
        if output:
            # truncate very long output so LLM context doesn't explode
            lines = output.split('\n')
            if len(lines) > 50:
                output = '\n'.join(lines[:50]) + f"\n... ({len(lines)-50} more lines truncated)"
        return output or "Command executed with no output"
    except subprocess.TimeoutExpired:
        return "Command timed out after 15 seconds"
    except Exception as e:
        return f"Failed to run command: {e}"
        

# User can set their own SearXNG instance, falls back to a public one

_tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        response = _tavily.search(
            query=query,
            search_depth="advanced",   # follows links, not just snippets
            max_results=max_results,
            include_answer=True        # gives a synthesised answer too
        )
        return [
            {
                "title":   r.get("title", ""),
                "snippet": r.get("content", ""),
                "url":     r.get("url", "")
            }
            for r in response.get("results", []) if r.get("content")
        ]
    except Exception as e:
        return []
def _wikipedia_search(query: str) -> list[dict]:
    try:
        wiki = wikipediaapi.Wikipedia(
            language="en",
            user_agent="ppt-agent/1.0"
        )
        page = wiki.page(query)
        if page.exists():
            sections = []
            for s in page.sections[:4]:
                if s.text.strip():
                    sections.append({
                        "title": f"{page.title} — {s.title}",
                        "snippet": s.text[:600],
                        "url": page.fullurl
                    })
            if not sections:
                sections = [{
                    "title": page.title,
                    "snippet": page.summary[:800],
                    "url": page.fullurl
                }]
            return sections
    except Exception:
        return []
    return []

@tool("web_search", description="""Search the internet for current information.
Use for news, recent events, live data, or anything that may have changed since training.
Always cite the source URLs in your reply.""")

def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for information on a topic.
    Use this to find detailed, accurate content before writing any slide."""

    results = _tavily_search(query, max_results)

    if not results:
        results = _wikipedia_search(query)

    if not results:
        # Be explicit — do NOT let the agent fill the gap with training data
        return (
            f"WEB_SEARCH_FAILED: No results returned for '{query}'. "
            "Do not answer from training data. Tell the user the search returned "
            "no results and suggest they try again or rephrase."
        )

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] {r['title']}\n"
            f"    {r['snippet']}\n"
            f"    Source: {r['url']}"
        )
    return "\n\n".join(formatted)

@tool("fetch_page", description="""Fetch and read the full text content of a webpage URL.
Use this after web_search when you need to read the full article, not just the snippet.
Pass the URL from a web_search result.""")
def fetch_page(url: str) -> str:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "websearch-agent/1.0"})
        # Strip HTML tags — just get readable text
        from html.parser import HTMLParser
        class _Strip(HTMLParser):
            def __init__(self): super().__init__(); self.text = []
            def handle_data(self, d): self.text.append(d)
        p = _Strip(); p.feed(r.text)
        text = " ".join(p.text.split())
        return text[:4000]  # cap to avoid flooding context
    except Exception as e:
        return f"Could not fetch page: {e}"


@tool("query_system", description="""Run a read-only system query command in cmd. 
Use for checking system info, network status, running processes, installed software, 
wifi networks, disk usage, and similar information gathering tasks. 
Do NOT use for commands that change system state — use run_system_command instead.""")
def query_system(command: str) -> str:
    """
    Runs a read-only cmd command and returns the output.
    Args:
        command: the cmd command to run e.g. 'ipconfig', 'tasklist', 'netstat -an'
                 'winget list', 'systeminfo', 'ping google.com -n 4'
    """
    safe, reason = _is_safe(command)
    if not safe:
        return reason
    
    return _run_command(command)


@tool("run_system_command", description="""Run a system command in cmd that changes state — 
such as killing a process, installing software via winget, changing network settings, 
or scheduling a shutdown. Requires user confirmation for destructive actions.
Do NOT use for read-only queries — use query_system instead.""")
def run_system_command(command: str, confirmed: bool = False) -> str:
    """
    Runs a cmd command that modifies system state.
    Args:
        command: the cmd command to run
        confirmed: must be True if the user has explicitly confirmed the action.
                   For commands requiring confirmation, ask the user first.
    """
    safe, reason = _is_safe(command)
    if not safe:
        return reason
    
    if _needs_confirmation(command) and not confirmed:
        return f"This command requires confirmation: '{command}'. Please ask the user to confirm before proceeding."
    
    return _run_command(command)

def get_screen_resolution():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # width, height

def arrange_windows(app_names: list[str]):
    width, height = get_screen_resolution()
    time.sleep(1.5)

    handles = []
    for name in app_names:
        windows = gw.getWindowsWithTitle(name)
        if windows:
            # get the first window not already in handles
            for w in windows:
                if w._hWnd not in handles:
                    handles.append(w._hWnd)
                    break

    n = len(handles)
    if n == 0:
        return "No windows found to arrange"
    
    layouts = {
        1: [
            (0, 0, width, height)
        ],
        2: [
            (0, 0, width // 2, height),
            (width // 2, 0, width // 2, height)
        ],
        3: [
            (0, 0, int(width * 0.6), height),
            (int(width * 0.6), 0, int(width * 0.4), height // 2),
            (int(width * 0.6), height // 2, int(width * 0.4), height // 2)
        ],
        4: [
            (0, 0, width // 2, height // 2),
            (width // 2, 0, width // 2, height // 2),
            (0, height // 2, width // 2, height // 2),
            (width // 2, height // 2, width // 2, height // 2)
        ]
    }
    
    positions = layouts.get(n, layouts[4])  # fallback to grid for 4+
    
    for hwnd, (x, y, w, h) in zip(handles, positions):
        # restore if minimized first
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP,
            x, y, w, h,
            win32con.SWP_SHOWWINDOW
        )
    
    return f"Arranged {n} windows"


@tool("get_current_volume", description="Use this tool to get the current system volume")
def get_current_volume() -> str:
    try:
        CoInitialize()
        device = AudioUtilities.GetSpeakers()
        if device:
            volume = device.EndpointVolume
        else:
            return "no audio device connected"
        current_vol = volume.GetMasterVolumeLevelScalar()
        CoUninitialize()
        return f"Current Volume for the device is: {int(current_vol*100)}"
    except Exception as e:
        return f"Error doing so: {e}"

@tool("volume control", description="Adjusts the per app volume or system volume")
def volume_control(
    vol_perc: int,
    target: str = "system"
) -> str:
    """
    Adjust system or application volume.

    Args:
        vol_perc: Volume percentage (0-100)
        target: "system" or process name such as
                "spotify.exe", "chrome.exe", "discord.exe"
    """

    try:
        CoInitialize()

        vol_perc = max(0, min(100, vol_perc))
        volume_scalar = vol_perc / 100

        # SYSTEM VOLUME
        if target.lower() == "system":
            device = AudioUtilities.GetSpeakers()

            if device is None:
                return "No audio device present"

            volume = device.EndpointVolume

            if volume.GetMute():
                volume.SetMute(0, None)

            volume.SetMasterVolumeLevelScalar(
                volume_scalar,
                None
            )

            return f"System volume set to {vol_perc}%"

        # APP VOLUME
        sessions = AudioUtilities.GetAllSessions()

        for session in sessions:
            process = session.Process

            if process is None:
                continue

            if process.name().lower() == target.lower():

                simple_volume = session.SimpleAudioVolume

                if simple_volume.GetMute():
                    simple_volume.SetMute(0, None)

                simple_volume.SetMasterVolume(
                    volume_scalar,
                    None
                )

                return (
                    f"{process.name()} volume set "
                    f"to {vol_perc}%"
                )

        return f"{target} is not playing any audio"

    except Exception as e:
        return f"Something went wrong: {e}"

    finally:
        CoUninitialize()    
@tool("Mute/unmute_Device", description="Use this tool to mute or unmute the audio device")
def mute_device() -> str :
    """
        Use this tool to mute or unmute the device.
    """
    try:
        CoInitialize()
        device = AudioUtilities.GetSpeakers()
        if device is not None:
            volume = device.EndpointVolume
        else:
            return f"No Audio device present"
        
        if volume.GetMute():
            volume.SetMute(0, None)
        else:
            volume.SetMute(1, None)
        CoUninitialize()
        return "device has been muted"
    except Exception as e:
        return f"Error muting/unmuting the device: {e}"
    
@tool("play/pause_media", description="Use this to play or pause media on the device")
def pause_media() -> str :
    try:
        pyautogui.press('playpause')
        return "I have paused/unpaused the media"
    except Exception as e:
        return f"Failed to pause/unpause the media: {e}"

@tool("set active window", description="""Use this tool ONLY to bring an already running 
                                            application to the foreground. The app must already be open and running. 
                                            Do NOT use this to launch or start an application.""")
def set_active_window(name_of_app: str) -> str:
    """
        Args:
            name_of_app: provide the name of the app you want to be shown at the top.
                        example: chrome
    """
    try:

        chrome_window = gw.getWindowsWithTitle(name_of_app)[0]
        if chrome_window is not None:
            chrome_window.activate()
            return "updated the active winow"
        else:
            return "No window by this name please check the name and try again"
    except Exception as e:
        return f"{e}"    

@tool("get_screen_brightness", description="Use this tool to get the current brightness level of the screen")
def get_screen_brightness() -> str:
    try:
        val = sbc.get_brightness()
        return f"Your current screen brightness is {val}"
    except Exception as e:
        return f"cant do it as {e}"
    
@tool("adjust_screen_brightness", description="Use this tool to adjust the screen brightness")
def adjust_screen_brightness(brightness_value: int) -> str:
    try:
        sbc.set_brightness(brightness_value)
        return f"Your screen brightness has been set to {brightness_value}"
    except Exception as e:
        return f"problem changing screen brightness {e}"
    
@tool("list_all_saved_profile_names", description="Use this tool to fetch the name of all the custom profiles saved")
def list_all_saved_profiles_names() -> list:
    try: 
        folder_path = PROFILES_DIR
        if os.path.exists(folder_path):
            file_list = os.listdir(folder_path)
        else:
            return ["Error no profiles folder exists"]
        return file_list
    except Exception as e:
        return [f'Error in fetching the list {e}']
    
@tool("save_user_defined_settings", description="Use this tool to save a user profile as json for future reference")
def save_profile(
    volume_level: int,
    screen_brightness: int,
    apps: list[dict],
    profile_name: str
) -> str:
    """
    Save a named profile with system settings.
    Args:
        volume_level: volume percentage 0-100
        screen_brightness: brightness percentage 0-100
        apps: list of dicts — MUST include 'url' key for any browser app opening a website
        Example with URLs:
              e.g. [{"name": "chrome", "url": "https://youtube.com"},
                    {"name": "chrome", "url": "https://docs.google.com"},
                    {"name": "notepad", "url": null}]
        profile_name: name to save the profile under
        Never omit the 'url' key — use null if no URL is needed
    """
    try:
        os.makedirs(r"../profiles", exist_ok=True)
        profile_dict = {
            "apps": apps,
            "screen_brightness": screen_brightness,
            "volume_level": volume_level
        }
        file_path = os.path.join(PROFILES_DIR, f"{profile_name}.json")
        with open(file_path, "w") as f:
            json.dump(profile_dict, f, indent=4)
        app_summary = ", ".join(
            f"{a['name']}" + (f" ({a['url']})" if a.get("url") else "")
            for a in apps
        )
        return f"Saved profile '{profile_name}': {app_summary}"
    except Exception as e:
        return f"Error saving the profile: {e}"
    
@tool("read_profile", description="use this tool to read the profile you want")
def read_profile(profile_name: str) -> str:
    """
    NOTE: DO NOT ADD FILE EXTENSION TO THE PROFILENAME JUST PROVIDE THE FILE NAME 
        FOR EXAMPLE:
        gaming is correct, gaming.json is incorrect
    """
    print("here")
    try:
        filepath = os.path.join(PROFILES_DIR, f"{profile_name}.json")

        with open(filepath, "r") as f:
            data = f.read()
        return data
    except Exception as e:
        return f"{e}"
    
@tool("del_profile", description="use this to delete a particular profile")
def del_profile(profile_name: str, got_confirmation: bool) -> str:
    try:
        filepath = os.path.join(PROFILES_DIR, f"{profile_name}.json")
        if got_confirmation:
            os.remove(filepath)
        else:
            return "please get confrmation from the user before deleting the file as this process is not reversable"
        return f"Successfully deleted the file: {profile_name}"
    except Exception as e:
        return f"{e}"

def _resolve_lnk(lnk_path: str) -> str:
    """Resolves a .lnk shortcut to its target executable path"""
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(lnk_path)
    return shortcut.Targetpath

def get_installed_apps():
    apps = {}

    # SOURCE 1 — Start Menu shortcuts (installed apps)
    start_menu_paths = [
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs")
    ]
    for path in start_menu_paths:
        for lnk in glob.glob(os.path.join(path, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(lnk))[0].lower()
            apps[name] = lnk

    # SOURCE 2 — Registry (formally installed programs)
    reg_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for reg_path in reg_paths:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    name = winreg.QueryValueEx(subkey, "DisplayName")[0].lower()
                    try:
                        install_loc = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                        if install_loc:
                            apps[name] = install_loc
                    except:
                        pass
                except:
                    continue
        except:
            continue

    # SOURCE 3 — Windows System32 built-ins (notepad, calc, mspaint etc.)
    system_builtins = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
        "task manager": "taskmgr.exe",
        "file explorer": "explorer.exe",
        "command prompt": "cmd.exe",
        "powershell": "powershell.exe",
        "registry editor": "regedit.exe",
        "snipping tool": "snippingtool.exe",
        "wordpad": "wordpad.exe",
        "control panel": "control.exe",
    }
    apps.update(system_builtins)

    return apps

@tool("get_installed_apps", description="Returns a list of all installed applications on the system")
def get_installed_apps_tool() -> str:
    """Use this to find what apps are installed before opening one"""
    apps = get_installed_apps()
    return f"Installed apps: {', '.join(apps.keys())}"

APP_CACHE = get_installed_apps()


def _open_app_by_name(app_name: str, url: str = "") -> str:
    apps = APP_CACHE

    if app_name.lower() in apps:
        target = apps[app_name.lower()]
        try:
            args = [url] if url else []

            if target.endswith(".lnk"):
                exe_path = _resolve_lnk(target)
                if exe_path and os.path.exists(exe_path):
                    subprocess.Popen([exe_path] + args, creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    os.startfile(target)

            elif target.endswith(".exe"):
                subprocess.Popen([target] + args, creationflags=subprocess.CREATE_NEW_CONSOLE)

            else:
                exe_files = glob.glob(os.path.join(target, "*.exe"))
                if exe_files:
                    name_match = [e for e in exe_files if app_name.lower() in os.path.basename(e).lower()]
                    chosen = name_match[0] if name_match else exe_files[0]
                    subprocess.Popen([chosen] + args, creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    os.startfile(target)

            return f"Opened {app_name}" + (f" with {url}" if url else "")
        except Exception as e:
            return f"Found {app_name} but failed to open it: {e}"

    matches = [name for name in apps.keys() if app_name.lower() in name]
    if len(matches) == 1:
        return _open_app_by_name(matches[0], url)
    elif len(matches) > 1:
        return f"Multiple matches: {', '.join(matches)}. Be more specific."
    else:
        return f"No app found matching '{app_name}'. Call get_installed_apps to see what's available."

@tool("open_application", description="""Use this tool to launch applications that are 
not currently running. Accepts a list of app objects with name and optional url. 
If multiple apps are provided opens all and arranges them on screen automatically.
Do NOT use this if the app is already open — use set_active_window instead.""")
def open_application(apps: list[dict]) -> str:
    """
    Opens one or more applications and arranges them on screen.
    Args:
        apps: list of dicts with 'name' and optional 'url'
              e.g. [{"name": "chrome", "url": "https://youtube.com"},
                    {"name": "chrome", "url": "https://docs.google.com"},
                    {"name": "notepad", "url": null}]
              for a single app: [{"name": "notepad"}]
    """
    if isinstance(apps, str):
        # safety fallback if model passes a plain string
        apps = [{"name": apps}]

    results = []
    successfully_opened = []

    for app in apps:
        name = app.get("name", "")
        url = app.get("url") or ""
        if not name:
            continue
        result = _open_app_by_name(name, url)
        results.append(result)
        if "Opened" in result:
            successfully_opened.append(name)

    if len(successfully_opened) > 1:
        time.sleep(2)
        arrangement = arrange_windows(successfully_opened)
        results.append(arrangement)

    return "\n".join(results)

@tool("get_running_apps", description="""Check which applications are currently open and running.
Call this before deciding whether to use open_application, set_active_window, or kill_process.""")
def get_running_apps() -> str:
    try:
        # visible windows
        windows = [w.title for w in gw.getAllWindows() if w.title.strip()]
        
        # all running processes including tray/background apps
        processes = []
        seen = set()
        for p in psutil.process_iter(['name', 'status']):
            try:
                name = p.info['name']
                if name and name not in seen and p.info['status'] == 'running':
                    seen.add(name)
                    processes.append(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return (
            f"VISIBLE WINDOWS ({len(windows)}):\n" +
            "\n".join(f"  - {w}" for w in windows) +
            f"\n\nBACKGROUND/TRAY PROCESSES ({len(processes)}):\n" +
            "\n".join(f"  - {p}" for p in sorted(processes))
        )
    except Exception as e:
        return f"Error: {e}"
    

@tool("show_system_monitor", description="""Launch the interactive system resource monitor. 
Shows real-time CPU, RAM, disk, battery and top processes in a live terminal dashboard. 
Use when the user asks about system performance, what's using resources, battery status, 
or wants to monitor their system.""")
def show_system_monitor() -> str:
    try:
        dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.py")
        
        # try Windows Terminal first, fall back to cmd
        try:
            subprocess.Popen([
                "wt.exe", "--title", "System Monitor",
                sys.executable, dashboard_path
            ])
        except FileNotFoundError:
            # wt not available, use cmd
            subprocess.Popen(
                f'start cmd /k python "{dashboard_path}"',
                shell=True
            )
        return "System monitor launched"
    except Exception as e:
        return f"Failed to launch monitor: {e}"

@tool("kill_process", description="""Kill or close a running application by name.
Use when the user says 'close X', 'kill X', 'stop X', 'quit X'.
Always tell the user what you are about to close and confirm before calling this tool.""")
def kill_process(app_name: str) -> str:
    """
    Kills all processes matching the given app name.
    Args:
        app_name: name of the app e.g. 'whatsapp', 'wa', 'chrome', 'spotify'
    """
    search = app_name.lower().replace(" ", "").replace(".exe", "")
    
    # find matching processes first
    matches = []
    for p in psutil.process_iter(['pid', 'name']):
        try:
            pname = p.info['name'].lower().replace(" ", "").replace(".exe", "")
            if search in pname or pname in search:
                matches.append(p.info['name'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not matches:
        return f"No process found matching '{app_name}'. Use get_running_apps to check what is running."

    # use taskkill instead of psutil.kill() — handles AppContainer and Store apps
    unique_names = list(set(matches))
    results = []
    
    for proc_name in unique_names:
        result = subprocess.run(
            f'taskkill /F /IM "{proc_name}" /T',
            shell=True,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            results.append(f"Closed {proc_name}")
        else:
            results.append(f"Failed to close {proc_name}: {result.stderr.strip()}")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Window resize / reposition presets
# Each entry is (x_pct, y_pct, w_pct, h_pct) as fractions of screen size
# ---------------------------------------------------------------------------
_WINDOW_PRESETS: dict[str, tuple[float, float, float, float]] = {
    # halves
    "left-half":      (0.0,  0.0,  0.5,  1.0),
    "right-half":     (0.5,  0.0,  0.5,  1.0),
    "top-half":       (0.0,  0.0,  1.0,  0.5),
    "bottom-half":    (0.0,  0.5,  1.0,  0.5),
    # quarters
    "top-left":       (0.0,  0.0,  0.5,  0.5),
    "top-right":      (0.5,  0.0,  0.5,  0.5),
    "bottom-left":    (0.0,  0.5,  0.5,  0.5),
    "bottom-right":   (0.5,  0.5,  0.5,  0.5),
    # full / centred
    "maximized":      (0.0,  0.0,  1.0,  1.0),
    "centered":       (0.25, 0.1,  0.5,  0.8),
    # thirds
    "left-third":     (0.0,         0.0, 1/3,  1.0),
    "center-third":   (1/3,         0.0, 1/3,  1.0),
    "right-third":    (2/3,         0.0, 1/3,  1.0),
    # two-thirds
    "left-two-thirds":  (0.0,       0.0, 2/3,  1.0),
    "right-two-thirds": (1/3,       0.0, 2/3,  1.0),
}

# Aliases so the LLM can use natural phrases
_PRESET_ALIASES: dict[str, str] = {
    "left":           "left-half",
    "right":          "right-half",
    "top":            "top-half",
    "bottom":         "bottom-half",
    "full":           "maximized",
    "fullscreen":     "maximized",
    "max":            "maximized",
    "centre":         "centered",
    "center":         "centered",
    "middle":         "centered",
    "top left":       "top-left",
    "top right":      "top-right",
    "bottom left":    "bottom-left",
    "bottom right":   "bottom-right",
    "left third":     "left-third",
    "center third":   "center-third",
    "right third":    "right-third",
    "left 2/3":       "left-two-thirds",
    "right 2/3":      "right-two-thirds",
}


def _find_window_handle(title_fragment: str) -> tuple[int, str] | tuple[None, str]:
    """
    Find a visible window whose title contains title_fragment (case-insensitive).
    Returns (hwnd, matched_title) or (None, error_message).
    """
    fragment = title_fragment.lower()
    all_windows = gw.getAllWindows()
    candidates = [w for w in all_windows if fragment in w.title.lower() and w.title.strip()]

    if not candidates:
        titles = [w.title for w in all_windows if w.title.strip()]
        return None, (
            f"No visible window found matching '{title_fragment}'. "
            f"Open windows: {', '.join(titles[:20])}"
        )

    # prefer exact / shortest match
    candidates.sort(key=lambda w: len(w.title))
    return candidates[0]._hWnd, candidates[0].title


@tool(
    "resize_window",
    description="""Move and/or resize a visible application window.
Use when the user says things like:
  'move chrome to the left half', 'snap notepad to the right',
  'make spotify take up 50 percent', 'put chrome in the top-right corner',
  'resize chrome to 60 percent width'.

Parameters
----------
window_title : str
    Part of the window title to identify it, e.g. 'chrome', 'notepad', 'spotify'.
    Case-insensitive fuzzy match — you do NOT need the exact title.
preset : str, optional
    One of the named layout presets:
      left-half, right-half, top-half, bottom-half,
      top-left, top-right, bottom-left, bottom-right,
      left-third, center-third, right-third,
      left-two-thirds, right-two-thirds,
      maximized, centered
    Natural aliases also accepted: 'left', 'right', 'top', 'bottom',
    'full', 'center', 'top left', 'bottom right', etc.
    If preset is provided, the x/y/width/height_pct params are ignored.
x_pct : float, optional
    Left edge position as a fraction of screen width (0.0–1.0). Default 0.0.
y_pct : float, optional
    Top edge position as a fraction of screen height (0.0–1.0). Default 0.0.
width_pct : float, optional
    Window width as a fraction of screen width (0.0–1.0). Default 0.5.
height_pct : float, optional
    Window height as a fraction of screen height (0.0–1.0). Default 1.0.

Examples
--------
  resize_window(window_title="chrome", preset="left-half")
  resize_window(window_title="chrome", preset="right")
  resize_window(window_title="notepad", preset="top-right")
  resize_window(window_title="spotify", x_pct=0.1, y_pct=0.05, width_pct=0.4, height_pct=0.9)
""",
)
def resize_window(
    window_title: str,
    preset: str = "",
    x_pct: float = 0.0,
    y_pct: float = 0.0,
    width_pct: float = 0.5,
    height_pct: float = 1.0,
) -> str:
    """
    Repositions and resizes a window either by named preset or explicit percentages.
    """
    try:
        # ── 1. find the window ──────────────────────────────────────────────
        hwnd, match_info = _find_window_handle(window_title)
        if hwnd is None:
            return match_info  # error string

        # ── 2. resolve preset → fractions ──────────────────────────────────
        if preset:
            key = preset.strip().lower()
            key = _PRESET_ALIASES.get(key, key)          # normalise alias
            if key not in _WINDOW_PRESETS:
                available = ", ".join(sorted(_WINDOW_PRESETS.keys()))
                return (
                    f"Unknown preset '{preset}'. "
                    f"Available presets: {available}. "
                    f"Or use x_pct/y_pct/width_pct/height_pct for a custom size."
                )
            x_pct, y_pct, width_pct, height_pct = _WINDOW_PRESETS[key]

        # ── 3. clamp fractions to [0, 1] ───────────────────────────────────
        x_pct      = max(0.0, min(1.0, x_pct))
        y_pct      = max(0.0, min(1.0, y_pct))
        width_pct  = max(0.05, min(1.0, width_pct))
        height_pct = max(0.05, min(1.0, height_pct))

        # ── 4. compute pixel values ─────────────────────────────────────────
        screen_w, screen_h = get_screen_resolution()
        x = int(screen_w * x_pct)
        y = int(screen_h * y_pct)
        w = int(screen_w * width_pct)
        h = int(screen_h * height_pct)

        # ── 5. restore if minimised, then move+resize ───────────────────────
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            x, y, w, h,
            win32con.SWP_SHOWWINDOW,
        )

        preset_label = f" (preset: {preset})" if preset else ""
        return (
            f"Moved '{match_info}' to ({x}, {y}), size {w}×{h} px"
            f" — {int(width_pct*100)}% wide, {int(height_pct*100)}% tall"
            f"{preset_label}"
        )
    except Exception as e:
        return f"Failed to resize window: {e}"


@tool("get available resolutions", description="use this tool to get the available resolution for the current monitor")
def get_available_resolutions():
    available_resolutions = []
    index = 0
    print("it is being used")
    try:
        while True:
            mode = win32api.EnumDisplaySettings(None, index)
            available_resolutions.append({
                "index": index,
                "width": mode.PelsWidth,
                "height": mode.PelsHeight,
                "refresh": mode.DisplayFrequency
            })
            index += 1
        
    except Exception:
        pass
    _DEVMODE_CACHE.clear()
    grouped = defaultdict(list)
    for mode in available_resolutions:
        key = (mode["width"], mode["height"])
        grouped[key].append(mode["refresh"])
        _DEVMODE_CACHE[(mode["width"], mode["height"], mode["refresh"])] = mode["index"]

    lines = ["Available resolutions:"]
    for i, ((w, h), rates) in enumerate(sorted(grouped.items(), reverse=True), 1):
        rates_str = ", ".join(str(r) for r in sorted(set(rates)))
        lines.append(f"{i}. {w} x {h}  —  {rates_str} Hz")
    
    print(lines)

    return "\n".join(lines)

@tool("set resolution", description="use this tool to set the resolution of the display")
def set_resolution(width: int, height: int, refresh: int) -> str:
    try:
        raw_index = _DEVMODE_CACHE.get((width, height, refresh))
    except Exception as e:
        return "please run get_available_resolutions() first"
    print("You are here")
    if raw_index is None:
        return f"Resolution {width}x{height} @ {refresh}Hz not found — call get_available_resolutions first"
    mode = win32api.EnumDisplaySettings(None, raw_index)
    mode.DisplayFixedOutput = win32con.DMDFO_STRETCH
    mode.Fields |= win32con.DM_DISPLAYFIXEDOUTPUT
    result = win32api.ChangeDisplaySettings(mode, 0)
    if result == -2:  # stretch rejected by driver — retry without it
        mode = win32api.EnumDisplaySettings(None, raw_index)  # fresh mode, no flags
        result = win32api.ChangeDisplaySettings(mode, 0)


    if result == 0:
        return f"Resolution set to {width}x{height} @ {refresh}Hz"
    elif result == 1:
        return f"Resolution set to {width}x{height} @ {refresh}Hz — restart required"
    else:
        return f"Failed to set resolution (code {result})"


# ===========================================================================
# File management tools  (powered by file_manager.py)
# ===========================================================================


def _safe_path(user_path: str) -> "tuple[str, str | None]":
    """Resolve a workspace-relative path; block path traversal."""
    base = os.path.abspath(WATCHED_FOLDER)
    resolved = os.path.abspath(os.path.join(base, user_path))
    if not resolved.startswith(base + os.sep) and resolved != base:
        return "", f"Access denied: '{user_path}' is outside the workspace."
    return resolved, None


# ── read ──────────────────────────────────────────────────────────────────────

@tool("read_file", description="""Read the full text content of a file in the agent workspace.
Use when the user says 'read X', 'show me X', 'what is in X', 'open X'.
The path is relative to the workspace root (e.g. 'notes.txt' or 'src/main.py').""")
def read_file(filepath: str) -> str:
    """
    Args:
        filepath: path relative to the workspace root, e.g. 'notes.txt'
    """
    abs_path, err = _safe_path(filepath)
    if err:
        return err
    if not os.path.isfile(abs_path):
        return f"File not found: '{filepath}'"
    return read_file_content(abs_path)


# ── write / create ────────────────────────────────────────────────────────────

@tool("write_file", description="""Write (or overwrite) a file in the agent workspace.
Use when the user says 'write X to file', 'save this as X', 'update file X',
'create a file called X with ...'. Creates parent folders automatically.
Re-indexes the file for semantic search after writing.""")
def write_file(filepath: str, content: str) -> str:
    """
    Args:
        filepath: path relative to the workspace root, e.g. 'notes.txt'
        content:  full text content to write into the file
    """
    abs_path, err = _safe_path(filepath)
    if err:
        return err
    try:
        os.makedirs(os.path.dirname(abs_path) or abs_path, exist_ok=True)
        if get_file_type(filepath) == ".docx" or get_file_type(filepath) == "docx":
            write_docx(abs_path, content)
        else:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        index_file(abs_path)
        return f"Written and indexed: '{filepath}'"
    except Exception as e:
        return f"Failed to write '{filepath}': {e}"


# ── delete ────────────────────────────────────────────────────────────────────

@tool("delete_file", description="""Permanently delete a file or empty folder from the workspace.
Use when the user says 'delete X', 'remove file X', 'get rid of X'.
Always confirm with the user before calling this tool.""")
def delete_file(filepath: str) -> str:
    """
    Args:
        filepath: path relative to the workspace root
    """
    abs_path, err = _safe_path(filepath)
    if err:
        return err
    if not os.path.exists(abs_path):
        return f"Not found: '{filepath}'"
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            remove_file_from_index(abs_path)
            return f"Deleted file: '{filepath}'"
        elif os.path.isdir(abs_path):
            os.rmdir(abs_path)
            return f"Deleted empty folder: '{filepath}'"
        return f"Cannot delete: '{filepath}'"
    except OSError as e:
        return f"Failed to delete '{filepath}': {e}"


# ── move / rename ─────────────────────────────────────────────────────────────

@tool("move_file", description="""Move or rename a file/folder inside the workspace.
Use when the user says 'rename X to Y', 'move X to folder Y', 'reorganise X'.
Both paths are relative to the workspace root.""")
def move_file(source: str, destination: str) -> str:
    """
    Args:
        source:      relative path of the file/folder to move
        destination: relative destination path
    """
    src, err = _safe_path(source)
    if err:
        return err
    dst, err = _safe_path(destination)
    if err:
        return err
    if not os.path.exists(src):
        return f"Source not found: '{source}'"
    try:
        os.makedirs(os.path.dirname(dst) or dst, exist_ok=True)
        _shutil.move(src, dst)
        remove_file_from_index(src)
        if os.path.isfile(dst):
            index_file(dst)
        return f"Moved '{source}' → '{destination}'"
    except Exception as e:
        return f"Failed to move '{source}': {e}"


# ── list ──────────────────────────────────────────────────────────────────────

@tool("list_files", description="""List files and subfolders in the workspace (or a subfolder).
Use when the user says 'list files', 'what files do I have', 'show me the workspace',
'what is in the X folder'. Pass an empty string to list the workspace root.""")
def list_files(subfolder: str = "") -> str:
    """
    Args:
        subfolder: relative path inside the workspace to list (empty = root)
    """
    if subfolder:
        abs_path, err = _safe_path(subfolder)
        if err:
            return err
    else:
        abs_path = os.path.abspath(WATCHED_FOLDER)

    if not os.path.isdir(abs_path):
        return f"Not a directory: '{subfolder}'"
    try:
        entries = sorted(os.scandir(abs_path), key=lambda e: (not e.is_dir(), e.name.lower()))
        if not entries:
            return "Empty folder."
        lines = []
        for entry in entries:
            icon = "📁" if entry.is_dir() else "📄"
            size = f"  ({entry.stat().st_size:,} B)" if entry.is_file() else ""
            lines.append(f"{icon} {entry.name}{size}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list '{subfolder}': {e}"


# ── name / extension search ───────────────────────────────────────────────────

@tool("search_files", description="""Search for files in the workspace by name or extension.
Use when the user says 'find files named X', 'find all .py files', 'where is file X'.
Returns matching relative paths.""")
def search_files(query: str) -> str:
    """
    Args:
        query: filename fragment or extension to match, e.g. 'report' or '.py'
    """
    base = os.path.abspath(WATCHED_FOLDER)
    matches = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if query.lower() in fname.lower():
                matches.append(os.path.relpath(os.path.join(root, fname), base))
    if not matches:
        return f"No files found matching '{query}'."
    return "\n".join(sorted(matches))


# ── semantic content search ───────────────────────────────────────────────────

@tool("search_file_content", description="""Semantically search the content of indexed workspace files.
Use when the user says 'find files about X', 'search my files for X',
'which file mentions X', 'look for X in the workspace'.
Returns the most relevant file excerpts ranked by relevance.""")
def search_file_content(query: str, top_k: int = 5) -> str:
    """
    Args:
        query: natural language description of what to find
        top_k: max number of results to return (default 5)
    """
    try:
        query_vec = _np.array(_fm_embedder.embed_query(query), dtype=_np.float32)
        conn = _fm_get_conn()
        rows = conn.execute("""
            SELECT c.content, c.embedding, d.filepath
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
        """).fetchall()
        conn.close()

        if not rows:
            return "No files have been indexed yet. Try reading or writing a file first."

        base = os.path.abspath(WATCHED_FOLDER)
        scored = []
        for content, emb_bytes, filepath in rows:
            if emb_bytes is None:
                continue
            stored = _np.frombuffer(emb_bytes, dtype=_np.float32)
            score = _cosine_sim(query_vec, stored)
            rel = os.path.relpath(filepath, base)
            scored.append((score, rel, content))

        scored.sort(reverse=True, key=lambda x: x[0])
        results = []
        for score, rel, content in scored[:top_k]:
            snippet = content[:300].replace("\n", " ")
            results.append(f"[{score:.2f}] {rel}\n  {snippet}")
        return "\n\n".join(results) if results else "No relevant content found."
    except Exception as e:
        return f"Search failed: {e}"
    
@tool("getWATCHED_FOLDER_tree", description="""Show the full recursive folder/file tree of the agent workspace.
Use when the user says 'show me the folder structure', 'what does the workspace look like',
'give me an overview of the files', or before doing complex file operations so you understand
the layout. Returns an indented tree with file sizes.""")
def getWATCHED_FOLDER_tree(subfolder: str = "", max_depth: int = 6) -> str:
    """
    Args:
        subfolder: subfolder to root the tree at (empty = whole workspace)
        max_depth: how many levels deep to recurse (default 6)
    """
    base = os.path.abspath(WATCHED_FOLDER)
    if subfolder:
        root = os.path.abspath(os.path.join(base, subfolder))
        if not root.startswith(base):
            return "Access denied: path is outside the workspace."
    else:
        root = base
 
    if not os.path.isdir(root):
        return f"Not a directory: '{subfolder or 'workspace'}'"
 
    lines = [f"📁 {os.path.basename(root)}/"]
 
    def _walk(path: str, prefix: str, depth: int):
        if depth > max_depth:
            lines.append(f"{prefix}    ... (max depth reached)")
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}    [permission denied]")
            return
 
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
 
            if entry.is_dir():
                lines.append(f"{prefix}{connector}📁 {entry.name}/")
                _walk(entry.path, child_prefix, depth + 1)
            else:
                size = entry.stat().st_size
                size_str = f"{size:,} B" if size < 1024 else f"{size/1024:.1f} KB"
                lines.append(f"{prefix}{connector}📄 {entry.name}  ({size_str})")
 
    _walk(root, "", 1)
    return "\n".join(lines)