#!/usr/bin/env python
# Note - need to add "--extension-pkg-allow-list=win32security, win32api" to pylint
# settings to avoid setting off unsafe ctypes warning.
# cspell:ignore pywintypes, typeshed, superceded, WINFUNCTYPE, powrprof, LASTINPUTINFO
# cspell:ignore dotenv, _MEIPASS, SYSTEMPOWERSTATUS, STANDBYIDLE, HIBERNATEIDLE
# cspell:ignore Wavelink, Elgato, Segoeui, creationflags, winrt
"""Implements a simple suspend (sleep/hibernate) forcing mechanic for Windows.

Functions
- Minimal class that monitors idle time and sleeps or hibernates after SLEEP_AFTER.
"""
from argparse import ArgumentParser
import ctypes
from ctypes import wintypes
from enum import StrEnum, Enum
from pathlib import Path
from subprocess import run as run_sub, Popen, CREATE_NO_WINDOW
import sys
from time import sleep
import threading
from typing import Any, Callable
from PIL import Image, ImageDraw, ImageFont
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)
from psutil import process_iter, Process
from tray_manager import (  # type: ignore[import-untyped]
    TrayManager,
    Button,
    CheckBox,
    Label,
)
from tomli_w import dump as dump_toml
import win32api
import win32security

# Note: I'm using the .get() methods of IAsyncOperation to avoid running an asyncio
# event loop within threads. This seems sensible as I'm only calling the awaitables
# infrequently, and because of the way I'm using them, blocking is a non-issue.
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSession as Session,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

M_TO_SECONDS = 60
# Per https://github.com/pydantic/pydantic-settings/issues/259, use a global
# hack to allow cli config file location.
CONFIG_FILE_PATH: Path | None = None
CONFIG_FILE = "config.toml"

# Strings for tray menu items
ENABLED = "Enabled"
DISABLED = "Disabled"
USE_SYSTEM_TIMERS = "Use System Timers"
SUSPEND_STATE = "Suspend state"
SUSPEND_AFTER = "Suspend after"
RESUME_PLAYBACK = "Resume playback"
SUSPEND_SOON = "Suspend now(-ish)!"
EXIT = "Exit"


class SuspendState(StrEnum):
    """Suspend states."""

    # Using strenum as toml can handle this.
    SLEEP = "sleep"
    HIBERNATE = "hibernate"
    # And for suspending suspend states, or when neither are available.
    DISABLED = "disabled"


####################################################
# BETA Functionality. May remove this in future.
class RestartType(StrEnum):
    """Process restart types.

    Beta functionality. Supports limited restarting of processes following a resume.
    """

    # For now, only supporting a basic subprocess.Popen call.
    POPEN = "Popen"
    ####################################################


class PowerStatus(Enum):
    """Power state for system."""

    BATTERY = 0
    AC_MODE = 1
    UNKNOWN = 255


def tray_icons() -> tuple[Image.Image, Image.Image]:
    """Return minimal enabled/disabled icons for sleeper_service."""
    # I'd prefer to make the background transparent, but white will have to do to
    # make things safe for light and dark interfaces.
    # (An alpha of zero would be better!
    # image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    disabled = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(disabled)

    # Ugly trial and error
    font_size = 60
    font = ImageFont.truetype("C:/Windows/fonts/Segoeui.ttf", font_size)
    draw.text((15, 11), "Z", font=font, fill="black", stroke_width=0, anchor="lt")

    font_size = 24
    font = ImageFont.truetype("C:/Windows/fonts/Segoeui.ttf", font_size)
    draw.text((5, 20), "Z", font=font, fill="black", stroke_width=0, anchor="lt")
    font_size = 16
    font = ImageFont.truetype("C:/Windows/fonts/Segoeui.ttf", font_size)
    draw.text((45, 30), "Z", font=font, fill="black", stroke_width=0, anchor="lt")

    enabled = disabled.copy()

    draw.line((5, 5, 59, 59), "red", 3)
    draw.line((5, 59, 59, 5), "red", 3)

    return (enabled, disabled)


class Settings(BaseSettings):
    """Rough and ready settings via pydantic."""

    # Service is suspended when enabled is false.
    enabled: bool = True
    # When using system settings, sleep/hibernate time is pulled from powercfg
    use_system_timer: bool = True
    # Manual timer if not using system timer. Minimum valid value is 60 seconds.
    manual_suspend_after: int | float = 600  # seconds
    manual_suspend_state: SuspendState = SuspendState.SLEEP
    check_interval: int | float = 60  # seconds
    # Default state of resume playback button.
    resume_playback: bool = False
    # Wait time after resuming from sleep before trying to resume playback (s).
    resume_playback_delay: int = 1  # Seconds!
    suspend_button: bool = False
    ####################################################
    # BETA Functionality. May remove this in future.
    restarts: dict[str, RestartType] = {}
    ####################################################
    _config_path: Path | None = None

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
        global CONFIG_FILE_PATH  # pylint: disable=W0603
        if not CONFIG_FILE_PATH:
            # Use the same location as the source file per pyinstaller docs for
            # one folder app.
            if getattr(sys, "frozen", False):
                # we are running in a bundle
                # sys._MEIPASS returns pyinstaller _internal folder.
                # For a bundle, we will put the file in the parent folder with
                # the bundle exe.
                # pylint: disable-next=protected-access
                config_folder = Path(sys._MEIPASS).parent  # type:ignore[attr-defined]
            else:
                # We are running in a normal Python environment
                # As we have no better info, store the config in the cwd
                config_folder = Path().cwd()

            # Fix the global constant.
            CONFIG_FILE_PATH = config_folder / CONFIG_FILE

        # Only toml settings allowed.
        return (TomlConfigSettingsSource(settings_cls, toml_file=CONFIG_FILE_PATH),)

    def model_post_init(self, context: Any, /) -> None:
        """Fix unsafe settings and resave."""
        # It's a really bad idea to allow this to be too short!
        self.manual_suspend_after = max(self.manual_suspend_after, 60)
        # Anything less than a second is bananas (also a lazy fix for v0.20 update).
        self.check_interval = max(self.check_interval, 1)
        self.save()

    def save(self) -> None:
        """Write settings to the default location."""
        # Assertion should never be triggered.
        assert CONFIG_FILE_PATH is not None
        with open(CONFIG_FILE_PATH, "wb") as fp:
            dump_toml(self.model_dump(), fp)


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


class SYSTEMPOWERSTATUS(ctypes.Structure):
    """Structure for return from GetSystemPowerStatus (SYSTEM_POWER_STATUS)."""

    # From
    # https://stackoverflow.com/questions/21083518/get-battery-status-using-wmi-in-python
    # and
    # https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getsystempowerstatus
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("Reserved1", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class SleeperService:
    """Monitors idle timer, forces suspend in line with active settings."""

    # Settings, shared across threads.
    _settings: Settings
    # Active suspend parameter set.
    _suspend_after: int
    _check_interval: int
    _suspend_state: SuspendState

    # It'e not clear if the tray should be its own thing or managed entirely
    # by the sleeper_service instance. But as it is so tightly enmeshed, for
    # now I'm going manage it entirely within the instance.
    _tray: TrayManager
    _menu_items: dict[str, Button | CheckBox]
    # Variables shared between tray and service
    # Alway use lock around these.
    # Set to True by tray to trigger main loop exit.
    _exit_flag: bool = False
    _shared_enabled_state: bool
    _shared_use_system_state: bool
    _shared_resume_playback: bool

    _lock: threading.Lock
    _state_change: threading.Event

    # pywin32 stuff.
    _last_input_info: LASTINPUTINFO
    # Class callables
    _callables_defined: bool = False
    _set_suspend_state: Callable
    _get_tick_count: Callable
    _get_last_input_info: Callable
    _get_system_power_status: Callable

    def __init__(self, settings: Settings) -> None:
        """Create api methods used in class."""
        if not self._callables_defined:
            self._create_api_methods()

        # also create the last input info struct, as we might as well only have the one
        # instance.
        self._last_input_info = LASTINPUTINFO()
        # cb_size should be defined in LASTINPUTINFO(), but it's beyond my skills.
        # pylint: disable-next=[attribute-defined-outside-init]
        self._last_input_info.cb_size = ctypes.sizeof(self._last_input_info)

        # Process the settings.
        self._settings = settings

        # Deal with settings that can only be modified by editing the settings file.
        self._check_interval = int(self._settings.check_interval)

        # Set up thread objects for comms and initialise shared values
        self._lock = threading.Lock()
        self._state_change = threading.Event()
        self._shared_enabled_state = self._settings.enabled
        self._shared_use_system_state = self._settings.use_system_timer
        self._shared_resume_playback = self._settings.resume_playback

        # Flag for quick suspend.
        self._quick_suspend = threading.Event()

        # And build the system tray.
        self._create_tray()

    def _create_tray(self) -> None:
        """Create and populate the system tray with current settings."""
        # Create variable dependent menu items before creating tray.
        # (Thread safe to do afterwards, but it reads more clearly this way).
        self._menu_items = {}
        self._menu_items[ENABLED] = CheckBox(
            ENABLED,
            default=True,
            check_default=self._shared_enabled_state,
            checked_callback=self._update_state,
            unchecked_callback=self._update_state,
        )
        self._menu_items[USE_SYSTEM_TIMERS] = CheckBox(
            USE_SYSTEM_TIMERS,
            check_default=self._shared_use_system_state,
            checked_callback=self._update_state,
            checked_callback_args=(False,),
            unchecked_callback=self._update_state,
            unchecked_callback_args=(False,),
        )
        self._menu_items[SUSPEND_STATE] = Label(text="      Suspend: disabled")
        self._menu_items[SUSPEND_AFTER] = Label(text="      After: 0s")
        self._menu_items[RESUME_PLAYBACK] = CheckBox(
            RESUME_PLAYBACK,
            check_default=self._shared_resume_playback,
            checked_callback=self._update_state,
            unchecked_callback=self._update_state,
        )
        if self._settings.suspend_button:
            self._menu_items[SUSPEND_SOON] = Button(
                SUSPEND_SOON, callback=self._quick_suspend.set
            )

        self._menu_items[EXIT] = Button(EXIT, callback=self._update_state, args=(True,))

        self._tray = TrayManager("Sleeper Service", run_in_separate_thread=True)

        icons = tray_icons()
        self._tray.load_icon(icons[0], ENABLED)
        self._tray.load_icon(icons[1], DISABLED)
        self._update_icon()

        # Adding the menu from the main thread. Which is thread safe
        # as nothing happens until the menu is active anyway
        menu = self._tray.menu
        for item in self._menu_items.values():
            menu.add(item)

        # Finally, trigger an update when the main loop starts.
        self._state_change.set()

    def _update_icon(self) -> None:
        """Set icon to match enabled state."""
        if self._shared_enabled_state:
            self._tray.set_icon(ENABLED)
        else:
            self._tray.set_icon(DISABLED)

    def _update_state(self, tray_exit: bool = False) -> None:
        """Update states and update flag to notify sleeper service of state change."""
        with self._lock:
            # Only update shared variables here.
            # Service will manage settings update.
            # A tiny bit of doubling up.
            self._shared_enabled_state = self._menu_items[ENABLED].get_status()
            self._update_icon()
            self._shared_use_system_state = self._menu_items[
                USE_SYSTEM_TIMERS
            ].get_status()
            self._shared_resume_playback = self._menu_items[
                RESUME_PLAYBACK
            ].get_status()

            if tray_exit:
                # Could do this in a separate routine, but I prefer to keep all of the
                # changes under one lock call.
                # Clean up tray and flag main loop to halt.
                # Not sure how the magic works, but the net outcome of the kill call is
                # the tray manager loop ends and the tray manager loop is terminated.
                self._tray.kill()
                self._exit_flag = True

        # Notify sleeper service of state change.
        self._state_change.set()

    def _update_suspend_settings(self) -> None:
        """Update service suspend timer and state based on tray settings."""
        if not self._settings.use_system_timer:
            # In manual mode, we assume the user knows what they are doing. Almost no
            # checks. At all.
            self._suspend_after = int(self._settings.manual_suspend_after)
            self._suspend_state = self._settings.manual_suspend_state
        else:
            # Read system settings.
            # Default to not suspending with an hour long timer.
            self._suspend_state = SuspendState.DISABLED
            self._suspend_after = 60 * M_TO_SECONDS

            # Will need AC/battery info. Don't use this often, so call on the fly.
            power_status = SYSTEMPOWERSTATUS()
            self._get_system_power_status(ctypes.byref(power_status))
            power_state = PowerStatus(power_status.ACLineStatus)

            # Figure out which suspend states takes precedence.
            suspend_after = self._get_idle_times(power_state, SuspendState.SLEEP)
            if suspend_after > 0:
                # Set sleep parameters.
                self._suspend_after = suspend_after
                self._suspend_state = SuspendState.SLEEP

            if self._hibernate_enabled:
                suspend_after = self._get_idle_times(
                    power_state, SuspendState.HIBERNATE
                )
                if suspend_after > 0:
                    if (
                        self._suspend_state == SuspendState.DISABLED
                        or suspend_after < self._suspend_after
                    ):
                        # Either sleep is not active, or hibernate time is more
                        # conservative
                        self._suspend_after = suspend_after
                        self._suspend_state = SuspendState.HIBERNATE

        # Update icon text.
        self._menu_items[SUSPEND_STATE].edit(
            text=f"      Suspend: {self._suspend_state}"
        )
        self._menu_items[SUSPEND_AFTER].edit(
            text=f"      After: {self._suspend_after}s"
        )

    @staticmethod
    def _get_idle_times(power_state: PowerStatus, idle_type: SuspendState) -> int:
        """Use powercfg to get active sleep after/hibernate after value."""
        if idle_type == SuspendState.SLEEP:
            alias = "STANDBYIDLE"
        elif idle_type == SuspendState.HIBERNATE:
            alias = "HIBERNATEIDLE"
        else:
            raise ValueError(f"Invalid idle type '{idle_type}'.")

        output = run_sub(
            "powercfg /query SCHEME_CURRENT SUB_SLEEP " + alias,
            check=True,
            text=True,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        ).stdout.splitlines()

        for line in output:
            if "Power Setting Index: " in line:
                parts = line.strip().split(" ")
                if parts[1] == "AC":
                    ac_value = int(parts[-1], 16)
                else:
                    dc_value = int(parts[-1], 16)
                    break

        if power_state == PowerStatus.AC_MODE:
            active_value = ac_value
        elif power_state == PowerStatus.BATTERY:
            active_value = dc_value
        else:
            # Use the most conservative if in an unknown power state.
            active_value = min(dc_value, ac_value)
            if active_value == 0:
                # Annoyingly, 0 is never, so:
                active_value = max(dc_value, ac_value)

        return active_value

    @property
    def _hibernate_enabled(self) -> bool:
        """Check if hibernate is usable."""
        output = run_sub(
            "powercfg /a",
            check=True,
            text=True,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        ).stdout.splitlines()

        for line in output:
            clean = line.strip()
            if clean.startswith("The following sleep states are not available"):
                # If we haven't found it, we aren't going to.
                break
            if clean == "Hibernate":
                return True
        return False

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

        # GetSystemPowerStatus needed for AC/DC operating state.
        prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.POINTER(SYSTEMPOWERSTATUS))
        cls._get_system_power_status = prototype(
            ("GetSystemPowerStatus", ctypes.windll.kernel32)
        )

        cls._callables_defined = True

    def suspend(self) -> None:
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
        # Preliminaries:
        if self._suspend_state == SuspendState.DISABLED:
            # no-op if suspend is disabled.
            return

        if self._suspend_state == SuspendState.HIBERNATE:
            hibernate = True
        else:
            hibernate = False

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

    def _check_state(self) -> bool:
        """Update service variables based on tray state.

        Returns true if the tray is still running, false if exit_flag is true.
        """
        if self._state_change.is_set():
            with self._lock:
                self._settings.enabled = self._shared_enabled_state
                self._settings.use_system_timer = self._shared_use_system_state
                self._settings.resume_playback = self._shared_resume_playback
                # Save on the basis things might have changed. Will happen
                # very infrequently.
                self._settings.save()
                if self._exit_flag:
                    # Tray is done, we've cleaned up settings changes, so
                    # might as well exit now.
                    return False

            # Clear the flag so the tray can work again and so we don't repeat.
            self._state_change.clear()

            # Apply changes to the service settings.
            self._update_suspend_settings()

        return True

    @staticmethod
    def _playback_status(manager: MediaManager) -> dict[Session, bool]:
        """Return dict of playback status for each session."""
        last_playback_status = {}
        for session in manager.get_sessions():
            # Use .get method to convert async to blocking sync
            # mp = session.try_get_media_properties_async().get()
            # Could use session id, but why not just hash on
            # session object?
            # session.source_app_user_model_id
            last_playback_status[session] = (
                session.get_playback_info().playback_status == PlaybackStatus.PLAYING
            )

        return last_playback_status

    def _restart_playback(self, last_playback_status: dict[Session, bool]) -> None:
        """Restarts playback on System Media Transport Control players."""
        sleep(self._settings.resume_playback_delay)

        for session, was_playing in last_playback_status.items():
            # After resume, some players will have a status of playing even though
            # playback is paused/suspended/indeterminate state.
            # So we'll try a brute force pause-then-play to restart play.
            if was_playing:
                session.try_pause_async().get()
                # Small wrinkle - even with the force pause play, player states may
                # not catch up as quickly as they should, and if the player
                # receives a play commend when it thinks it is already playing, it
                # will ignore it. So we create an ugly event loop to wait until the
                # pause is confirmed before restarting playback.
                # (All of this might be alleviated by using asyncio, but a) I don't
                # want to learn it now and b) if feels like a sledgehammer/nut
                # solution).
                # If we don't get there after 5 seconds, probably not going to happen.
                # As we don't want to end up blocking, try the play command and
                # move on to the next app.
                counter = 0
                while (
                    session.get_playback_info().playback_status != PlaybackStatus.PAUSED
                    and counter < 50
                ):
                    sleep(0.1)
                    counter += 1
                session.try_play_async().get()

    def _suspend_pending(self) -> bool:
        """Return true when idle time exceeds suspend time or quick suspend is set."""
        return (
            self._settings.enabled
            and not self._suspend_state == SuspendState.DISABLED
            and self.idle_time() > self._suspend_after
        ) or self._quick_suspend.is_set()

    def main_loop(self) -> None:
        """Execute main loop for class."""
        # This is the main loop that should be run as separate thread?

        # Create session manager for playback resume.
        # Use .get method to convert async to blocking sync
        manager = MediaManager.request_async().get()

        while self._check_state():
            sleep(self._check_interval)
            if self._suspend_pending():
                if self._settings.resume_playback:
                    # Get media player states.
                    playback_status = self._playback_status(manager)

                if self._suspend_pending():
                    # As getting the playback status can take a bit of time,
                    # do a second check that to ensure we are still waiting on a
                    # suspend. This (hopefully mostly) deals with the problem that when
                    # both sleeper_service and system sleep are active, we can get
                    # system and sleeper_service triggering one after the other.
                    # If suspend fails, we'll just try again next cycle.
                    self.suspend()

                    # Reinstate playback
                    if self._settings.resume_playback:
                        self._restart_playback(playback_status)

                    ####################################################
                    # BETA Functionality. May remove this in future.
                    # This is where we need to do things like restart programs/apps
                    # after resuming from suspend. E.g.
                    # subprocess.Popen("Elgato.Wavelink.exe")
                    # If I go down this path, need to add a list of processes to
                    # settings and canned actions that will be applied to these.
                    # E.g.
                    # [resume]
                    # "Elgato.Wavelink.exe", "Popen"
                    # "Process 2", "Run"
                    # Do a look up here to decide how to handle each of these wake up
                    # calls.
                    # Not trying to be clever at all initially. Restart wavelink and
                    # call it a day.
                    for program, restart in self._settings.restarts.items():
                        if restart == RestartType.POPEN:
                            Popen(program)
                    ##############################################################

                    # Finally, sleep for 1 minute to allow for some user input and
                    # prevent firing a quick succession second sleep.
                    sleep(M_TO_SECONDS)

                # Finally, clear the quick suspend in case it was set previously.
                self._quick_suspend.clear()


def single_instance() -> bool:
    """Check if another instance of the command line is running."""
    retval = True

    # Check this instance against others.
    this = Process()
    this_pid = this.pid
    parent = this.parent()
    if parent:
        this_parent = parent.pid
    else:
        this_parent = -1

    this_name = this.name()
    this_cmdline = this.cmdline()

    processes = {
        p.pid: p.info
        for p in process_iter(["name", "cmdline"])
        if p.info['name'] == this_name
    }

    for pid, info in processes.items():
        if pid != this_pid:
            if info['cmdline'] == this_cmdline and this_parent != pid:
                # annoyingly, depending on how the python launcher is invoked, we
                # can end up with two processes with identical command lines, so ignore
                # parent of this process.
                # Unlikely to work properly when debugging, but I figure anyone doing
                # dev is old enough and ugly enough manage this.
                retval = False
                break

    return retval


if __name__ == "__main__":
    if not single_instance():
        sys.exit()

    parser = ArgumentParser(
        description="Simple suspend service to force hibernate/sleep after a set time."
    )

    parser.add_argument(
        "--config",
        metavar="CONFIG_PATH",
        help=(
            "Specify the configuration file/configuration folder path. If a folder is"
            " specified, the filename defaults to `config.toml`. Refer to the"
            " documentation for default file locations if this option is not used."
        ),
    )

    args = parser.parse_args()

    if args.config:
        config_path = Path(args.config).resolve()
        if config_path.is_file():
            CONFIG_FILE_PATH = config_path
        else:
            CONFIG_FILE_PATH = config_path / CONFIG_FILE

    # Shared settings.
    my_settings = Settings()
    sleeper = SleeperService(my_settings)
    sleeper.main_loop()

    # Belt and braces. Save current settings on exit.
    my_settings.save()
