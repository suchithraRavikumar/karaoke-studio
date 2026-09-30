@echo off
setlocal
cd /d "%~dp0"
for %%D in ("D:\Anaconda3" "D:\anaconda3" "D:\miniconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\Anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\anaconda3" "%ProgramData%\Anaconda3" "C:\anaconda3" "C:\Anaconda3") do (
  if not defined PYW if exist "%%~D\pythonw.exe" set "PYW=%%~D\pythonw.exe"
)
if not defined PYW for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
  echo Could not find Python. Run Setup.bat first.
  pause
  exit /b 1
)
del "%USERPROFILE%\Desktop\Karaoke Studio.lnk" >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Karaoke Studio.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0karaoke_studio.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0karaoke_studio.ico,0'; $s.Description='Karaoke Studio - make karaoke videos'; $s.Save()"
ie4uinit.exe -show >nul 2>nul
echo.
echo  "Karaoke Studio" shortcut with the new icon is on your Desktop.
echo  (If the old icon still shows, right-click the Desktop and choose Refresh.)
echo.
pause
