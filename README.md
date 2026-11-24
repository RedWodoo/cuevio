# Cuevio

A Windows terminal companion for video lectures in **KBTU UniNet**.

**Physics I (FUN1201)** · **Chrome** · **Playback at 1×**  
[Quick start](#quick-start) · [Troubleshooting](#troubleshooting) · [Support](#support)

Cuevio opens Chrome after you choose playback, finds the first unfinished lecture, and works through the course in the portal's order. It uses UniNet's own completion marks to skip credited videos and waits for the portal to confirm each new view before continuing.

## Quick start

You need Windows, Git and Python 3.10 or newer on your PATH, Google Chrome, and a KBTU account enrolled in Physics I.

**Install Cuevio on your desktop.** Open PowerShell, then copy and run this block.

~~~powershell
$cuevioDir = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Cuevio'
git clone https://github.com/RedWodoo/cuevio.git "$cuevioDir"
Set-Location -LiteralPath "$cuevioDir"
.\run-uninet.cmd
~~~

For later launches, open the **Cuevio** folder on your desktop and double-click **run-uninet.cmd**.

On the first launch, Cuevio installs its Python dependencies in the project folder and asks for your KBTU login and password if they have not been saved. Python 3.14 and newer show asterisks while you type. In older versions, the password prompt hides typed characters.

Cuevio then opens the menu. Enter **play** (1) to start playback, **settings** (3) to change playback options, or **setup** (4) to update your saved sign-in.

Choose **course** (6) to see the subject picker. Physics I is available, and the second entry is a demo for future courses.

| Command | What it does |
| --- | --- |
| .\run-uninet.cmd | Set up sign-in on the first launch, then open the menu |
| .\run-uninet.cmd --play | Start playback immediately with your saved settings |
| .\run-uninet.cmd --setup | Save or update the credentials on this computer |
| .\run-uninet.cmd --check | List completion marks and remaining videos without starting playback |
| .\run-uninet.cmd --version | Show the installed version |
| .\run-uninet.cmd --support | Open the project's support section |

## Playback

- Cuevio scans available weeks before playback. The queue bar tracks clips credited by UniNet, shows the current clip and its position, and estimates the remaining viewing time from the durations available in the portal.
- In **Settings**, choose whether to start muted and whether to stop after one credited video or continue through the queue.
- Type a command in the **cuevio ›** field and press **Enter**. Press **Tab** to complete a command or **↑** to recall one you entered.
- New videos play from the beginning. Partially watched videos continue from the position saved by UniNet when the playlist shows a resume time.
- The script uses the portal's own player controls and waits at normal speed. If the player fails to start, it can reselect the video or reload the page.
- Locked weeks are reported with their opening date. The queue handles video lectures only.
- When UniNet has credited every available video, Cuevio closes its Chrome window, shows what finished and which weeks are still locked, and returns to the main menu in an interactive terminal.
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
