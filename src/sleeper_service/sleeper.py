#!/usr/bin/env python
# Note - need to add "--extension-pkg-allow-list=win32security, win32api" to pylint
# settings to avoid setting off unsafe ctypes warning.
# cspell:ignore pywintypes, typeshed, superceded, WINFUNCTYPE, powrprof, LASTINPUTINFO
# cspell:ignore dotenv
"""Implements a simple sleep forcing mechanic for Windows.

Functions
- Minimal class that monitors idle time and sleeps or hibernates after SLEEP_AFTER.
"""
from time import sleep
from datetime import datetime
from typing import Any, Callable
import ctypes
from ctypes import wintypes
from pathlib import Path
import threading
from pydantic import PrivateAttr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)
from tomli_w import dump as dump_toml
import win32api
import win32security

M_TO_SECONDS = 60
# To save pain, this will always for config.
TOML_PATH = Path.home() / "AppData/Local/sleeper_service/config.toml"


class Settings(BaseSettings):
    """Rough and ready settings via pydantic."""

    # When using system settings, sleep/hibernate time is pulled from powercfg
    use_system_timer: bool = True
    # Manual timer if not using system timer
    manual_sleep_after: int = 10  # minutes
    check_interval: int = 1  # minutes
    _lock: threading.Lock = PrivateAttr()
    _update_flag: threading.Event = PrivateAttr()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customise settings to use toml file only."""
        return (TomlConfigSettingsSource(settings_cls),)

    def model_post_init(self, context: Any, /) -> None:
        """Resave settings, created threading objects."""
        self.save()
        self._lock = threading.Lock()
        self._update_flag = threading.Event()

    def save(self) -> None:
        """Write settings to the default location."""
        if not TOML_PATH.parent.exists():
            TOML_PATH.parent.mkdir()

        with open(TOML_PATH, "wb") as fp:
            dump_toml(self.model_dump(), fp)

    @property
    def lock(self) -> threading.Lock:
        """Provide lock object for context manager."""
        return self._lock

    @property
    def update_flag(self) -> threading.Event:
        """Provide settings update flag for thread notification."""
        return self._update_flag


class LASTINPUTINFO(ctypes.Structure):
    """Structure for GetLastInputInfo."""

    # The following commented __init__ is from
    # https://stackoverflow.com/questions/72887838/python-does-not-find-the-dwtime-attribute-of-the-structure-class
    # which initialises cbSize automatically when the class is instanced.
    # Given I don't understand it, I'll stick with the old school method of
    # setting the size after instancing (see below). Come back to this as my
    # understanding improves.
    # def __init__(self, dwTime=0):
    #    super().__init__(ct.sizeof(self.__class__), dwTime)

    _fields_ = (
        ("cb_size", wintypes.UINT),
        ("dw_time", wintypes.DWORD),
    )


class SleeperService:
    """Monitors idle timer, forces sleep in line with power settings."""

    # Settings, shared across threads.
    _settings: Settings
    _sleep_after: int
    _check_interval: int

    # This group may disappear later.
    _hibernate: bool

    # pywin32 stuff.
    _last_input_info: LASTINPUTINFO
    # Class callables
    _callables_defined: bool = False
    _set_suspend_state: Callable
    _get_tick_count: Callable
    _get_last_input_info: Callable

    def __init__(self, settings: Settings) -> None:
        """Create api methods used in class."""
        self._settings = settings
        self.reload_settings()

        # Pending setup:
        #   - Read registry to get Hibernate vs sleep state, and time to to sleep.
        # For now, force to false, use SLEEP_AFTER constant.
        self._hibernate = False
        if not self._callables_defined:
            self._create_api_methods()

        # also create the last input info struct, as we might as well only have the one
        # instance.
        self._last_input_info = LASTINPUTINFO()
        # cb_size should be defined in LASTINPUTINFO(), but it's beyond my skills.
        # pylint: disable-next=[attribute-defined-outside-init]
        self._last_input_info.cb_size = ctypes.sizeof(self._last_input_info)

    def reload_settings(self) -> None:
        """Read and process settings (thread safe)."""
        with self._settings.lock:
            if self._settings.use_system_timer:
                pass
            else:
                self._sleep_after = self._settings.manual_sleep_after * M_TO_SECONDS

            self._check_interval = self._settings.check_interval * M_TO_SECONDS

            self._settings.update_flag.clear()

    @classmethod
    def _create_api_methods(cls) -> None:
        """Create various windows api methods used by the class."""
        # Prototypes for ctypes. I'm not sure if it is pythonic to make these module
        # globals, but it also doesn't feel inappropriate either.
        # This is absolutely overkill for this  but learning how to do a windows dll
        # call properly. The ctypes dll call is based on code from
        # https://stackoverflow.com/questions/50669907/how-to-use-ctypes-errcheck
        # param flags are overkill, so skipped here.
        prototype = ctypes.WINFUNCTYPE(
            wintypes.INT,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.BOOL,
        )
        # Set up suspend call. As the failure state of this function is sleep/hibernate
        # doesn't happen, no need for error code. (We'll handle by doing another
        # wait cycle and trying again).
        cls._set_suspend_state = prototype(("SetSuspendState", ctypes.windll.powrprof))

        # Not error checking tick count, as it doesn't!
        prototype = ctypes.WINFUNCTYPE(
            wintypes.DWORD,
        )
        # Get tick count has no error state, so no need for errcheck.
        cls._get_tick_count = prototype(("GetTickCount", ctypes.windll.kernel32))

        prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.POINTER(LASTINPUTINFO))
        # See idle_time for error handling.
        cls._get_last_input_info = prototype(("GetLastInputInfo", ctypes.windll.user32))

        cls._callables_defined = True

    def suspend(self, hibernate: bool = False) -> None:
        """Force sleep or hibernate for Windows.

        Parameters
        ----------
        hibernate: bool, default False
            If False (default), system will enter Suspend/Sleep/Standby state.
            If True, system will Hibernate, but only if Hibernate is enabled in the
            system settings. If it's not, system will fall back to Sleep.

        Example:
        --------
        >>> suspend()
        """
        # Initially based on code from
        # https://stackoverflow.com/questions/7517496/sleep-suspend-hibernate-windows-pc.
        # However, that code uses win32api.SetSystemPowerState, which is superceded by
        # SetSuspendState.
        # https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setsystempowerstate
        # "Applications written for Windows Vista and later should use SetSuspendState
        # instead"
        # https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-setsuspendstate
        # So I've updated accordingly.

        # Enable the SeShutdown privilege (which must be present in your
        # token in the first place). Unlike the suspend state setup, do this every time
        # (privileges should not change, but just in case!)
        privilege_flags = (
            win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY
        )
        process_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), privilege_flags
        )
        privilege_value = win32security.LookupPrivilegeValue(
            "", win32security.SE_SHUTDOWN_NAME
        )
        restore_privileges = win32security.AdjustTokenPrivileges(
            # pywin32 typeshed doesn't yet provide enough info for type check the tuple
            # list
            process_token,
            0,
            [(privilege_value, win32security.SE_PRIVILEGE_ENABLED)],  # type:ignore
        )

        # This call could fail, but we ignore it and try again on the basis that
        # it just adds another idle cycle without sleep.
        self._set_suspend_state(hibernate, True, False)

        # Restore privileges
        win32security.AdjustTokenPrivileges(process_token, 0, restore_privileges)
        win32api.CloseHandle(process_token)

    def idle_time(self) -> float:
        """Return approximate time without user input in seconds."""
        result = self._get_last_input_info(ctypes.byref(self._last_input_info))

        if result != 0:
            idle_ms = self._get_tick_count() - self._last_input_info.dw_time
        else:
            # Error in GetLastInputInfo. Assume timer is reset.
            idle_ms = 0

        return idle_ms / 1000.0

    def main_loop(self) -> None:
        """Execute main loop for class."""
        # This is the main loop that should be run as separate thread?

        while True:
            sleep(self._check_interval)
            idle = self.idle_time()
            if idle > self._sleep_after:
                print(f"Sleeping at: {datetime.now()}")
                # If suspend fails, we'll just try again next cycle.
                self.suspend(self._hibernate)
                print(f"Waking at: {datetime.now()}")
            else:
                print(f"Idle for {idle} seconds.")


if __name__ == "__main__":
    # Shared settings.
    my_settings = Settings()
    my_settings.use_system_timer = False
    my_settings.manual_sleep_after = 2
    my_settings.check_interval = 0.25

    sleeper = SleeperService(my_settings)
    sleeper.main_loop()
