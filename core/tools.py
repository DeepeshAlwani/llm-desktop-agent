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

@tool("set active window", description="Use this tool to change the active window on the display")
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
    
@tool("save_user_defined_settings", description="Use this tool to save user profile as json for future reference")
def save_profile(volume_level: int, screen_brightness: int, application : str, profile_name: str) -> str:
    try:
        os.makedirs("profiles", exist_ok=True)
        profile_dict = {"Application": application, "Screen_Brightness": screen_brightness, "volume_level": volume_level}
        
        file_path = f"profiles/{profile_name}.json"
        with open(file_path, "w") as f:
            json.dump(profile_dict, f, indent=4)
        return f"Saved custom profile: {profile_name}"
    except Exception as e:
        return f"Error saving the profile: {e}"
    
@tool("read_profile", description="use this tool to read the profile you want")
def read_profile(profile_name: str) -> str:
    try:
        filepath = f"profiles/{profile_name}.json"

        with open(filepath, "r") as f:
            data = f.read()
        return data
    except Exception as e:
        return f"{e}"
    
@tool("del_profile", description="use this to delete a particular profile")
def del_profile(profile_name: str, got_confirmation: bool) -> str:
    try:
        filepath = f"profiles/{profile_name}.json"
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


def _open_app_by_name(app_name: str) -> str:
    """
    Opens an application on the system.
    Args:
        app_name: name of the app to open e.g. 'notepad', 'spotify', 'chrome'
    """
    apps = APP_CACHE

    if app_name.lower() in apps:
        target = apps[app_name.lower()]
        try:
            if target.endswith(".lnk"):
                exe_path = _resolve_lnk(target)
                if exe_path and os.path.exists(exe_path):
                    subprocess.Popen(exe_path)
                else:
                    # fallback — some .lnk files point to things 
                    # like UWP apps that have no direct exe
                    os.startfile(target)
            elif target.endswith(".exe"):
                subprocess.Popen(target)
            else:
                # directory from registry — try to find an exe inside
                os.startfile(target)
            return f"Opened {app_name}"
        except Exception as e:
            return f"Found {app_name} but failed to open it: {e}"

    matches = [name for name in apps.keys() if app_name.lower() in name]
    if len(matches) == 1:
        return _open_app_by_name(matches[0])
    elif len(matches) > 1:
        return f"Multiple matches: {', '.join(matches)}. Be more specific."
    else:
        return f"No app found matching '{app_name}'. Call get_installed_apps to see what's available."


@tool("open_application", description="Opens an installed application by name")
def open_application(app_name: str) -> str:
    """
    Opens an application on the system.
    Args:
        app_name: name of the app to open e.g. 'notepad', 'spotify', 'chrome'
    """
    return _open_app_by_name(app_name)
