@echo off
setlocal
title Karaoke Studio - one-time setup
cd /d "%~dp0"
echo.
echo  ==== Karaoke Studio setup ====
echo.
call :findpython
if not defined PY (
  echo  Could not find Python / Anaconda on this computer.
  echo  Install Anaconda from https://www.anaconda.com/download and run Setup.bat again.
  pause
  exit /b 1
)
echo  Using Python: %PY%
echo.
echo  [1/4] Installing pygame (plays the song inside the app)...
"%PY%" -m pip install --upgrade pygame
echo.
echo  [2/4] Installing / updating the YouTube downloader (yt-dlp)...
"%PY%" -m pip install --upgrade "yt-dlp[default]"
where deno >nul 2>nul && goto denook
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\deno.exe" goto denook
echo  Installing Deno (YouTube needs it to read video pages)...
winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
:denook
echo.
echo  [3/4] Checking ffmpeg (makes the videos)...
where ffmpeg >nul 2>nul && goto ffok
if defined PYROOT if exist "%PYROOT%\Library\bin\ffmpeg.exe" goto ffok
for /d %%G in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do goto ffok
echo  Installing ffmpeg with winget...
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
for /d %%G in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do goto ffok
if defined PYROOT if exist "%PYROOT%\Scripts\conda.exe" (
  echo  winget did not work - installing ffmpeg with conda instead ^(can take a few minutes^)...
  call "%PYROOT%\Scripts\conda.exe" install -y -c conda-forge ffmpeg
)
:ffok
echo  ffmpeg ready.
echo.
echo  [4/4] Creating a "Karaoke Studio" shortcut on your Desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Karaoke Studio.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0karaoke_studio.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0karaoke_studio.ico,0'; $s.Description='Karaoke Studio - make karaoke videos'; $s.Save()"
echo.
echo  All done! Open "Karaoke Studio" from your Desktop (or double-click "Karaoke Studio.bat").
echo  Tip: if YouTube links stop working one day, just run Setup.bat again to update.
echo.
pause
exit /b 0

:findpython
for %%D in ("D:\Anaconda3" "D:\anaconda3" "D:\miniconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\Anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\Anaconda3" "%ProgramData%\miniconda3" "C:\anaconda3" "C:\Anaconda3" "C:\miniconda3") do (
  if not defined PY if exist "%%~D\python.exe" (
    set "PYROOT=%%~D"
    set "PY=%%~D\python.exe"
    set "PYW=%%~D\pythonw.exe"
  )
)
if defined PY exit /b 0
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PY (
    set "PY=%%P"
    set "PYW=%%~dpPpythonw.exe"
  )
)
exit /b 0
