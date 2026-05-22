from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
from langchain.tools import tool


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

        volume = device.EndpointVolume

        min_vol = volume.GetVolumeRange()[0]

        set_vol = abs(min_vol) - abs(min_vol)/100 * float(vol_perc)

        print(set_vol)

        volume.SetMasterVolumeLevel(set_vol*(-1), None)

        CoUninitialize()

        return f"Volume Set to: {vol_perc}"
    except Exception as e:
        print(e)
        return f"Something went wrong: {e}"
    
