# Cuevio

A Windows terminal companion for video lectures in **KBTU UniNet**.

**Physics I (FUN1201)** · **Chrome** · **Playback at 1×**  
[Quick start](#quick-start) · [Troubleshooting](#troubleshooting) · [Support](#support)

Cuevio opens Chrome after you choose playback, finds the first unfinished lecture, and works through the course in the portal's order. It uses UniNet's own completion marks to skip credited videos and waits for the portal to confirm each new view before continuing.

## Quick start

You need Windows, Python 3.10 or newer on your PATH, Google Chrome, and a KBTU account enrolled in Physics I. Download and extract the repository, then open PowerShell in the extracted folder.

**Open the menu.**

    .\run-uninet.cmd

You can also double-click the launcher. On the first run it creates a local Python environment and installs the dependencies. In the command field, enter **setup** (4) to set up your KBTU sign-in, **settings** (3) to change playback settings, or **play** (1) to start playback.

Enter your KBTU username or email and password in the local terminal. Python 3.14 and newer show asterisks while you type. In older versions, the password prompt hides typed characters.

| Command | What it does |
| --- | --- |
| .\run-uninet.cmd | Open the menu |
| .\run-uninet.cmd --play | Start playback immediately with your saved settings |
| .\run-uninet.cmd --setup | Save or update the credentials on this computer |
| .\run-uninet.cmd --check | List completion marks and remaining videos without starting playback |
| .\run-uninet.cmd --version | Show the installed version |
| .\run-uninet.cmd --support | Open the project's support section |

## Playback

- In **Settings**, choose whether to start muted and whether to stop after one credited video or continue through the queue.
- Type a command in the **cuevio ›** field and press **Enter**. Press **Tab** to complete a command or **↑** to recall one you entered.
- New videos play from the beginning. Partially watched videos continue from the position saved by UniNet when the playlist shows a resume time.
- The script uses the portal's own player controls and waits at normal speed. If the player fails to start, it can reselect the video or reload the page.
- Locked weeks are reported with their opening date. The queue handles video lectures only.
- Close the Chrome window or press **Ctrl+C** in the launcher to stop. The next run reads the current progress from UniNet.

| Command | Shortcut | Action |
| --- | --- | --- |
| pause | p | Pause the video and the queue |
| play | r | Resume playback |
| mute | m | Turn sound off |
| sound | u | Turn sound on |
| stop | q | Close the playback session |
| help | h | Show command hints |

## Credentials and logs

The password is stored locally in **Windows Credential Manager**. Chrome submits it over HTTPS to the official **key.kbtu.kz** sign-in form when you log in. The script does not write credentials to project files or logs and does not upload logs. To remove your saved sign-in, open Windows Credential Manager and remove the generic credential named **KBTU UniNet Physics I**.

Playback and error messages are also saved to a rotating local log at **%LOCALAPPDATA%\UniNet Queue\logs\uninet.log**.
The same folder keeps playback preferences in **settings.json**.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Chrome opens but sign-in fails | Run setup again with your current KBTU credentials. |
| A lecture is missing | Use check mode to see its status or opening date in UniNet. |
| Playback stops after a position jump | The log records the before and after positions and elapsed time. Restart the queue to load the latest progress saved by UniNet. |

The last entries are available in PowerShell.

    Get-Content "$env:LOCALAPPDATA\UniNet Queue\logs\uninet.log" -Tail 30

When sharing an error report, a short excerpt with the error and lecture number is enough.

## Support

If Cuevio helped, [give it a star on GitHub](https://github.com/RedWodoo/cuevio). Bug reports and reproducible playback issues are welcome in [Issues](https://github.com/RedWodoo/cuevio/issues).
