"""The machine's IANA time zone on Windows, macOS and Linux.

Stdlib only. detect() returns the zone name or None and never raises; whether the name can be used
on this machine is the caller's question (pace.py asks its load_zone).
"""
import ctypes
import os
import re
from pathlib import Path

UTC_NAMES = {"UTC", "Etc/UTC", "Universal", "Etc/Universal", "Zulu", "Etc/Zulu"}
# Area/City, with optional further levels such as America/Argentina/Buenos_Aires.
IANA_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)+")
WINDOWS_ZONE_KEY = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
ICU_BUFFER = 128


def normalise(name):
    """The IANA name, `UTC` for its aliases, or None for anything that does not look like a zone."""
    if not isinstance(name, str):
        return None
    name = name.strip().lstrip(":").strip()
    if name in UTC_NAMES:
        return "UTC"
    # ICU answers Etc/Unknown when it cannot tell the zone.
    if name == "Etc/Unknown":
        return None
    return name if IANA_NAME.fullmatch(name) else None


def from_zoneinfo_path(path):
    """The zone a path into a zoneinfo tree names, such as the target of /etc/localtime."""
    path = str(path).replace("\\", "/")
    marker = "zoneinfo/"
    at = path.rfind(marker)
    if at < 0:
        return None
    name = path[at + len(marker):]
    for prefix in ("posix/", "right/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return normalise(name)


def _call(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - every failing source reads as "not detected"
        return None


def _icu():
    # Windows 10 1903 and later ship ICU as icu.dll; earlier versions have none.
    return ctypes.WinDLL("icu.dll")


def _icu_result(call):
    buf = ctypes.create_unicode_buffer(ICU_BUFFER)
    err = ctypes.c_int(0)
    length = call(buf, ctypes.byref(err))
    # ICU errors are positive; negative values are warnings and still give a result.
    if err.value > 0 or length <= 0:
        return None
    return buf.value[:length]


def icu_default_zone():
    """The default zone ICU gives on Windows, with the user's region applied."""
    fn = _icu().ucal_getDefaultTimeZone
    fn.restype = ctypes.c_int32
    fn.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int)]
    return _icu_result(lambda buf, err: fn(buf, ICU_BUFFER, err))


def icu_zone_for_windows_id(windows_id):
    """The IANA zone ICU maps a Windows zone name to, for the default region."""
    fn = _icu().ucal_getTimeZoneIDForWindowsID
    fn.restype = ctypes.c_int32
    fn.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_char_p, ctypes.c_wchar_p, ctypes.c_int32,
                   ctypes.POINTER(ctypes.c_int)]
    return _icu_result(lambda buf, err: fn(windows_id, -1, None, buf, ICU_BUFFER, err))


def registry_zone_name():
    """The Windows zone name of this machine, for example `Romance Standard Time`."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_ZONE_KEY) as key:
        value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
    return str(value).strip("\x00 ") or None


def _read_text(path):
    return Path(path).read_text(encoding="utf-8")


def _detect(environ, windows, read_link, read_text, icu_default, icu_for_windows_id, registry_zone):
    name = normalise(_call(environ.get, "TZ"))
    if name:
        return name
    if windows:
        name = normalise(_call(icu_default))
        if name:
            return name
        key = _call(registry_zone)
        return normalise(_call(icu_for_windows_id, key)) if key else None
    target = _call(read_link, "/etc/localtime")
    name = from_zoneinfo_path(target) if target else None
    if name:
        return name
    text = _call(read_text, "/etc/timezone")
    lines = text.splitlines() if isinstance(text, str) else []
    return normalise(lines[0]) if lines else None


def detect(environ=None, windows=None, read_link=None, read_text=None, icu_default=None,
           icu_for_windows_id=None, registry_zone=None):
    """The machine's IANA zone, or None. Every source is a parameter with the real one as default."""
    try:
        return _detect(os.environ if environ is None else environ,
                       os.name == "nt" if windows is None else windows,
                       read_link or os.readlink, read_text or _read_text,
                       icu_default or icu_default_zone, icu_for_windows_id or icu_zone_for_windows_id,
                       registry_zone or registry_zone_name)
    except Exception:  # noqa: BLE001 - detection must never fail the command that asks
        return None
