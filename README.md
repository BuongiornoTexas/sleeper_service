<!---
# cspell: ignore venv sleeperservice Elgato pystray pydantic pycaw  Popen pyinstaller
# cspell: ignore Voicemeeter pypi appxpackage appsfolder startapps requestsoverride
---> 

# TODO

Implementation:
- <del>Bare bones timer to force sleep.</del>
- Update module docs as I go.
- <del>Lock down pypi package name. </del>
- <del>Create pydantic-settings model for settings<del>
- <del>Initial thread elements<del>
- Test that file location works correctly with pyinstaller bundle, cli option.
- <del>Extract "Sleep after" parameter value from active power plan.<del>
- Add option too keep awake if audio stream detected on specific devices.
- Add pystray system tray icon.
- setup .toml file location.
- Delete note about watching for first release.
- Delete testing hacks in main
- Delete print() statements
- Forcing suspend breaks requests - I think this is a side effect that doesn't matter.

Right now, wave link breaks things badly on resume from sleep. If Elgato don't fix
this, may need to:
- (best case) restart wave link each time.
- check if wave link is active, kill and restart.

For finding and restarting Wave Link:
  - Can get more info with get-appxpackage -Name Elgato.Wavelink and get-startapps
  - If the final release doesn't fix problems in the beta, may need to kill the 
  wave link process and restart - kill+psutils or taskkill?
  - Finally, restart wave link with either qualified app name. 
    subprocess.run("explorer shell:appsfolder\\Elgato.WaveLink_g54w8ztgkx496!App")
  - Or, even better: Wave Link has an app execution alias: "Elgato.WaveLink.exe", so
  no need to find UWP name if this is active!
    - So far, it looks like the easiest method to start this is with:
    ```subprocess.Popen("Elgato.Wavelink.exe")```

Notes:
- To enable/disable hibernate: admin shell->powercfg /hibernate on/off
- To test hibernate status: admin shell->powercfg /a
- Look into using pycaw to enable/disable microphones/inputs as a way to kill 
sleep blocking by powercfg requests. Specifically, using privacy & security setting for
microphone. Maybe disabling device works as well. Neither sound like good solutions
though.


# sleeper_service

This  is a minimal Windows tray utility that enables (forces) sleep based on the active
power plan "Sleep After" parameter. 

For a long rant on why this exists, see the [Package Rationale](#package-rationale). 

For the short version of why this exists, if you are looking for something/anything
that deals with the "Legacy Kernel Caller" blocking sleep problem, hopefully this will
work for you.

I'm implementing this in my spare time (hopefully only a couple of weeks to get up), so
if you are interested, I suggest watch the repository for releases only and you'll be
notified when the first version is available. 

# Change Log

**v0.0.1** Proof of concept. 
**v0.0.2** Functional beta. Still requires pystray wrapper.. 

# Configuration file

A `config.toml` file provides manual configuration for sleeper_service. The default
location for this file is in the same folder as the sleeper_service.exe file
(pyinstaller version), or in the same folder as sleeper.py (pip version). This file
is created automatically on first run. 

The configuration options are:

- `user_system_timer`: `true` or `false`. If `true`, the suspend timer and suspend state
are read from the users Windows Power plan (`Sleep after` and `Hibernate after` values,
with the shorter timer assumed to apply). If `false`, manually specified values are
taken from the configuration file. 
- `manual_suspend_after`: time in minutes to activate suspend if `use_system_timer` is 
`false`.
- `manual_suspend_state`: The suspend state to apply if `use_system_timer` is `false`. 
Allowable case sensitive values are: "sleep", "hibernate" or "disabled".
- `check_interval`: time in minutes that sleeper_service will sleep between checks that
the idle time has expired (I'm assuming most people will be happy checking once a
minute at most).

# Package Rationale

This utility deals with the brain dead Windows implementation that allows an audio
stream to block sleep. (Truly. It's genuinely stupid.)

Typical symptoms of this problem are:
- A call to powercfg /requests will include the lines:
  ```
  [SYSTEM]
  An audio stream is currently in use.
  [DRIVER] Legacy Kernel Caller
  ```
- Windows ignores sleep settings in the power plan (yeah, it's really this stupid).
- There are no easy fixes or overrides to address the problem.

I've run into this problem with Elgato's Wave Link software (which is the trigger for
writing this utility), and it has been a problem with Voicemeeter in the past (not sure
if this has been resolved in more recent versions), and plenty of other software that
creates an audio stream. 

A quick search brings a vast range of complaints about Microsoft's bone headed
implementation, but little in the way of effective, simple solutions to the problem. In
particular:
- Using `powercfg /requestsoverride` should allow users to prevent the `Legacy Kernel 
Caller` from blocking sleep. This flat out doesn't work. (Even if it did, Microsoft has
decreed that this particular powercfg call requires elevated privileges. For a user
space problem. Did I mention bone headed?).
- There are various solutions using AutoHotKey, Visual Basic Scripts, and the Windows
task manager. All are a bit opaque. 

So this is yet another solution to the problem which is hopefully be relatively
fire and forget, and also easy to suspend for the times you actually do want an
audio stream to block sleep (rarely in my experience).
