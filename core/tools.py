from pycaw.pycaw import AudioUtilities
from comtypes import CoInitialize, CoUninitialize
from langchain.tools import tool
import pyautogui
import screen_brightness_control as sbc
import json
import os

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