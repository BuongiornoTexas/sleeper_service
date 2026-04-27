<!---
# cspell: ignore venv sleeperservice Elgato pystray pydantic pycaw  Popen pyinstaller
# cspell: ignore Voicemeeter pypi appxpackage appsfolder startapps requestsoverride
# cspell: ignore Wavelink Deezer SMTC winrt
---> 

# sleeper_service

**Minor breaking v0.20.0 change** The `manual_suspend_after` and `check_interval` options
in the `config.toml` configuration file are now in seconds rather than minutes - please
update your values if you use this feature!

This  is a minimal Windows tray utility that enables (forces) sleep based on the active
power plan "Sleep After" parameter. 

For a long rant on why this exists, see the [Package Rationale](#package-rationale). 

For the short version of why this exists, if you are looking for something/anything
that deals with the "Legacy Kernel Caller" blocking sleep problem, hopefully this will
work for you. In particular, if you have issues with Elgato Wave Link killing sleep, 
this is intended to help.

The v0.9 release provides a tray utility to trigger sleep or hibernate.

The v0.20 release supports restarting media playback on resume from suspend.

## Beta: App/program Launch on Resume

**v0.10.0 Beta functionality** Use with caution. I have added a beta function that can
(re-)launch apps/programs when resuming from a suspension *triggered by
sleeper_service* (it does not work when sleep is triggered by the system or the start
menu). This capability uses python's subprocess Popen to launch programs.

I've added this functionality because it allows me to manage issues in the Elgato Wave
Link 3.0 beta. Specifically there are some people (myself included) who are having
problems with Wave Link resuming from sleep properly. In my case, Wave Link either
crashes (most of the time), or breaks mixes and/or channel/mix connections. I'm
hoping that Elgato will address this issue  as part of their beta.

In the interim, I'm using this function to restart Wave Link each time I resume from
sleep (my examples in this section are for Wave Link). This is  working well enough to
allow me to use the beta. The one downside is that Wave Link will always open a
foreground desktop window on resume, as the beta does not support opening minimised to
the tray. I'm assuming this will be addressed towards the end of the beta. If not, I
will tweak the restart functionality when the final version releases.

To set it up, run sleeper_service to create/update the settings file, then exit, and
edit the settings so that the `[restarts]` section of the `config.toml` file looks like: 
```
[restarts]
"Elgato.Wavelink.exe" = "Popen"
```
In my case "Elgato.Wavelink.exe" is a system alias for starting the UWP Wave Link 3.0
app. Please replace this with your app name. Currently, "Popen" is the only launch mode
suppported, so this is the only valid value. Finally, if you want to start more than
one app on resume, just add another line per app. 

Using my sample `[restarts]`, Wave Link should restart after every resume triggered by
*sleeper_service*. As noted previously, manual or other automatic resumes will not
trigger this restart. 

This functionality will work with program or app that:
- Can be launched with Popen call.
- Does not require admin privileges.
- Returns immediately from the Popen call.

This may be useful for other audio control apps that cause the "Legacy Kernel Caller"
problem - e.g. Voicemeeter or SteelSeries Sonar (I haven't tested either of these, but
I know it was an issue for older versions of Voicemeeter, and may still be?). If you do
test this successfully with apps other than Wave Link, can you please add a comment
to the end of this [issue](https://github.com/BuongiornoTexas/sleeper_service/issues/2)
so that I can update the list of programs known to restart correctly.

If you have a program that you can't get to work with `[restarts]`, please raise an 
[issue](https://github.com/BuongiornoTexas/sleeper_service/issues/) so that we can
address the problem.

We do have a live issue which may affect some programs/apps - any program/app restarted
after sleep will have the same Windows "Current Directory" as the sleeper_service. This
could be a problem if it relies on finding configuration or data files in the current
directory. I'm treating this as a lower priority enhancement for a future release. If
this is affecting you, please add a comment on this 
[issue](https://github.com/BuongiornoTexas/sleeper_service/issues/1) so that I know to
make it a higher priority.

# Change Log

- **v0.20.1** Minor changes that (hopefully) eliminate double sleep events.
- **v0.20.0** Added an option to restart media playback on resume from suspend. This is
useful for media streams that pause after losing internet connectivity (e.g. Deezer,
youtube).
- **v0.11.0** Added an option to add a "Suspend now!" button to the tray menu for 
testing. This can only be enabled by editing the `config.toml` settings.
- **V0.10.0** Converted to .pyw app, added beta function to enable restarting Wave Link
on resume from sleep.
- **v0.9.0** Tray manager interface, core sleep functions implemented.
- **v0.0.2** Functional beta. Still requires pystray wrapper. 
- **v0.0.1** Proof of concept. 

# Installation and Usage

sleeper_service is a system tray application for Windows. I'd recommend downloading the
zip file from the [Release](https://github.com/BuongiornoTexas/sleeper_service/releases)
page, unzipping and adding the executable to your start programs. However, if you
prefer, you can install via pip (`pip install sleeper_service`) and either create your
own pyinstaller bundle or run it from a local python installation or virtual
environment.

Right click on the sleeper_service icon to see the interface:

  ![Interface](./images/Interface.png)

Clicking on `Enabled` will enable/disable the service. You can also enable/disable by
left clicking on the sleeper_service icon. The icon will have a red cross when disabled.

You can also switch between using the system timers and a manual timer specification. 
The `Suspend:` and `After:` lines are information only to tell you what the suspend
action is and when it will be triggered.

Some media players automatically pause when the system is suspended, and will not
restart playing on resume from suspend (some like youtube will play out their buffer
and then pause). If you are like me, you will want your music to resume streaming
when you wake your PC. In which case, enabling `Resume playback` may sort your problem. 

The playback resume works adequately for me, but does have a couple of wrinkles:

- It uses the SMTC winrt API to check which apps were playing before suspending
and will try to restart playback of these apps after resuming from suspend.
- If your favourite media app doesn't support SMTC, it won't work. You may be able to
sort this out with a plugin for your app (see this 
[Modern Flyouts page](https://github.com/ModernFlyouts-Community/ModernFlyouts/blob/main/docs/GSMTC-Support-And-Popular-Apps.md)
for some hints, but unfortunately some older players just won't work.
- The resume playback function is a bit brute force to work around player and Windows
issues. Specifically, it waits a short period after resuming from suspend 
(`resume_playback_delay` setting) to ensure the system is up and running properly, and
then performs the following actions for each app that was playing before suspend:
  - **pauses** the app because some media players have a status of "Playing" after
  resuming from suspend even though the stream is paused!
  - Waits a short period.
  - Restarts playback. 

  Please raise an issues if you have a media player that a) supports SMTC and b) does 
  not resume playback correctly and I'll see what I can do to help.

- The default settings work fine for my main player (Deezer), but may fail for apps
that buffer significantly. For instance, youtube in Firefox may have a 10+ second
buffer. With the default settings on my system, the player ignores the media commands
until the buffer runs out (yeah, not great really!) and the player then stops/pauses.
The work around is to increase `resume_playback_delay` so that it is larger than the
buffer, at which point the pause/wait/play action works correctly. For my system, a
delay of 15-20 seconds works (irritating, but at least it works).

If you need to force suspend frequently for testing or other reasons, you can enable
a `Suspend now(-ish)!` button on the interface by editing the `config.toml` file and
setting `suspend_button=true` (this option is not available from the UI). When you hit
the button, the system will suspend at the end of the next `check_interval` (see
[Configuration file](#configuration-file)). 

There are a few other configuration parameters, but these only be changed by editing the
[configuration file](#configuration-file) - exit the service before editing. 

(I'm assuming most of the parameters will be changed infrequently, so not worth 
including in the tray interface - Please raise an issue if you would prefer more
parameters in the UI.)

# Configuration file

The `config.toml` file provides manual configuration for sleeper_service. The location
of the file is determined in the following priority order.
- If the command line option `--config <file path>` specifies a file, e service will use
the named file.
- If the command line option `--config <folder path>` specifies a folder, the service
will look for `config.toml` in the specified folder path.
- If the service is run from the pyinstaller bundle, it will look for `config.toml` in
the same folder as the service executable.
- If the service is run as a normal python routine, it will look for the fi`config.toml`
in the current working directory.

If the file does not exist, it will be created. 

The configuration file options are:

- `enabled`: `true` or `false`. Enable or disable the sleeper service. Switchable from
the system tray interface. Useful if you want to turn it off for a while.
- `user_system_timer`: `true` or `false`. If `true`, the suspend timer and suspend state
are read from the users Windows Power plan (`Sleep after` and `Hibernate after` values,
with the shorter timer assumed to apply). If `false`, manually specified values are
taken from the configuration file. . Switchable from the system tray interface.
- `manual_suspend_after`: time in seconds to activate suspend if `use_system_timer` is 
`false`. Values less than 60 seconds will be increased to 60 seconds automatically.
- `manual_suspend_state`: The suspend state to apply if `use_system_timer` is `false`. 
Allowable case sensitive values are: "sleep", "hibernate" or "disabled".
- `check_interval`: time in seconds that sleeper_service will sleep between checks that
the idle time has expired (I'm assuming most people will be happy checking once a
minute at most).
- `resume_playback`: `true` or `false`. If true, sleeper service will try to check
which apps were playing media before suspending and will try to resume playback after
resuming from suspend. 
- `resume_playback_delay`: How long sleeper service will wait after resuming from
suspension before trying to restart playback. May be useful for players with long
buffers that do not resume correctly. See 
[Installation and Usage](#installation-and-usage).
- `suspend_button`: when `true`, the tray menu includes a "Suspend now(-ish)!" button
that will suspend the system at the end of the next `check_interval`. Great for testing!
Suspend was immediate in **v0.11.0**, but from **v0.20.0** it is included in the event
loop to ensure it correctly triggers things like resuming media playback. The button is
not displayed if `false`.
- `[restarts]`: See 
[Beta: App/program Launch on Resume](#beta-appprogram-launch-on-resume).

The default configuration file is:
```
enabled = true
use_system_timer = true
manual_suspend_after = 600
manual_suspend_state = "sleep"
check_interval = 60
resume_playback = false
resume_playback_delay = 1
suspend_button = false

[restarts]
```

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

# Development/testing

This section is a collection of information that may be useful in developing/testing
future functionality.

## Powercfg Options

Some of these commands may require an elevated shell:

- `powercfg /hibernate on/off` to enable or disable hibernate.
- `powercfg /a` lists available sleep states for system (and also sleep states that are
not available/enabled).

## Sleep and Requests

Forcing sleep from the Windows menu or programmatically will clear sleep blocking
requests (`powercfg /requests`). Restarting audio streams seems to repair this when
required, so I'm not going to worry about this. (Ideally, Microsoft would fix the
underlying problem that audio streams should not have special privileges that block
sleep states).

# PyInstaller Builds

If you are building your own binary, I suggest starting with a clean environment and
install only  sleeper_service and pyinstaller to minimise  by-catch in the pyinstaller
bundle.

# TODO/In Progress

This section contains informal notes on pending implementation details and possible
future fixes/extensions.

Implementation:
- Update module docs as development progresses.
- Maybe add option too keep awake if audio stream detected on specific devices.
- Make/ask for help with better icon. Best described as a bit rubbish at the moment.

Right now, wave link breaks things badly on resume from sleep. If Elgato don't fix
this, each time we resume, we may need to:
- (best case) restart wave link. Currently in beta for the beta. Not ideal, as Wave Link
does not restart minimised.
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

Other:
- Look into using pycaw to enable/disable microphones/inputs as a way to kill 
sleep blocking by powercfg requests. Specifically, using privacy & security setting for
microphone. Maybe disabling device works as well. Neither sound like good solutions
though.
- Assuming Elgato fix the resume problem, it may be enough to simply pause/play to reset
audio. Or maybe trigger a Stream Deck call to enable/disable channel/mix connections?