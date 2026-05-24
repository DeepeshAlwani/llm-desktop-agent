import pyautogui
import json
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

@tool("volume control", description="Adjusts the system volume")
def volume_control(vol_perc: int) -> str :
    """
        Use this tool to adjust the volume of the system.

        Args:
            vol_perc: The volume percentage you want the system at.
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

        volume.SetMasterVolumeLevelScalar(vol_perc / 100, None)

        CoUninitialize()

        return f"Volume Set to: {vol_perc}"
    except Exception as e:
        print(e)
        return f"Something went wrong: {e}"
    
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
    
import pygetwindow  as gw

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
        apps: list of dicts with 'name' and optional 'url' keys
              e.g. [{"name": "chrome", "url": "https://youtube.com"},
                    {"name": "chrome", "url": "https://docs.google.com"},
                    {"name": "notepad", "url": null}]
        profile_name: name to save the profile under
    """
    try:
        os.makedirs("profiles", exist_ok=True)
        profile_dict = {
            "apps": apps,
            "screen_brightness": screen_brightness,
            "volume_level": volume_level
        }
        file_path = f"profiles/{profile_name}.json"
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
    print("here")
    try:
        filepath = rf"../profiles/{profile_name}.json"

        with open(filepath, "r") as f:
            data = f.read()
        return data
    except Exception as e:
        return f"{e}"
    
@tool("del_profile", description="use this to delete a particular profile")
def del_profile(profile_name: str, got_confirmation: bool) -> str:
    try:
        filepath = rf"../profiles/{profile_name}.json"
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
                    subprocess.Popen([exe_path] + args)
                else:
                    os.startfile(target)

            elif target.endswith(".exe"):
                subprocess.Popen([target] + args)

            else:
                exe_files = glob.glob(os.path.join(target, "*.exe"))
                if exe_files:
                    name_match = [e for e in exe_files if app_name.lower() in os.path.basename(e).lower()]
                    chosen = name_match[0] if name_match else exe_files[0]
                    subprocess.Popen([chosen] + args)
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

@tool("get_running_apps", description="""Use this tool to check which applications are 
                                         currently open and running on the system. 
                                         Call this before deciding whether to use open_application or set_active_window.""")
def get_running_apps() -> str:
    try:
        # get visible windows with titles
        windows = [w.title for w in gw.getAllWindows() if w.title.strip()]
        return f"Currently open windows: {', '.join(windows)}"
    except Exception as e:
        return f"Error getting running apps: {e}"
    