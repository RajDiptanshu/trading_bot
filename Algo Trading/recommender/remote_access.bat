@echo off
REM ============================================================================
REM  remote_access.bat  --  reach the cockpit from any of YOUR devices, privately
REM
REM  Uses Tailscale (a private VPN mesh). Only devices logged into your own
REM  Tailscale account can reach the app. Nothing is exposed to the public
REM  internet or to other machines on your local WiFi. The app has NO login of
REM  its own, so this privacy is what protects it -- do NOT use ngrok/public
REM  tunnels for this app.
REM
REM  ONE-TIME on this PC (do these yourself -- they need an admin prompt + a
REM  browser sign-in that a script cannot click for you):
REM     1) Install:  winget install tailscale.tailscale   (approve the UAC prompt)
REM     2) Sign in:  tailscale up   (a browser opens; use Google/GitHub/email)
REM  ON EACH OTHER DEVICE (phone, laptop): install the Tailscale app and sign in
REM  with the SAME account.
REM
REM  Then just double-click THIS file (with the cockpit running via start.bat).
REM ============================================================================
setlocal
set "TS=C:\Program Files\Tailscale\tailscale.exe"
if not exist "%TS%" set "TS=tailscale"

echo.
echo  [1/3] Checking Tailscale login...
"%TS%" status >nul 2>&1
if errorlevel 1 (
  echo.
  echo    Tailscale is not installed, or you are not signed in yet.
  echo    Run these once, then re-run this file:
  echo        winget install tailscale.tailscale      ^(approve the admin prompt^)
  echo        tailscale up                            ^(sign in when the browser opens^)
  echo.
  pause
  exit /b 1
)

echo  [2/3] Publishing the cockpit ^(127.0.0.1:8650^) to your private tailnet...
"%TS%" serve --bg http://127.0.0.1:8650
if errorlevel 1 (
  echo.
  echo    If the error mentions HTTPS/certificates, enable it once at:
  echo        https://login.tailscale.com/admin/dns
  echo    -> turn ON "MagicDNS" and "HTTPS Certificates", then re-run this file.
  echo.
  pause
  exit /b 1
)

echo  [3/3] Done. Your private address ^(open on any signed-in device^):
echo.
"%TS%" serve status
echo.
echo  Make sure the cockpit itself is running first ^(double-click start.bat^).
echo  To turn remote access OFF later:   "%TS%" serve reset
echo.
pause
