@echo off
setlocal

cd /d "%~dp0"

rem ================================
rem ProxyCanvas startup config
rem ================================
rem Leave *_CONDA_ENV empty to run with normal python from PATH.
rem If you use conda, set the env name yourself, for example:
rem set "BACKEND_CONDA_ENV=my_env"
rem set "CHATGPT2API_CONDA_ENV=my_env"

set "BACKEND_CONDA_ENV=zimg"
set "BACKEND_CMD=python -u app.py"

rem Optional external service: CLIProxyAPI.
rem Leave CLIPROXY_DIR empty to skip it.
rem Example:
rem set "CLIPROXY_DIR=D:\apps\CLIProxyAPI"
set "CLIPROXY_DIR=F:\CLIProxyAPI_6.10.9_windows_amd64"
set "CLIPROXY_EXE=cli-proxy-api.exe"
set "CLIPROXY_CMD=go run ./cmd/server"

rem Optional external service: ChatGPT2API.
rem Leave CHATGPT2API_DIR empty to skip it.
rem Example:
rem set "CHATGPT2API_DIR=D:\Code\chatgpt2api"
rem set "CHATGPT2API_CONDA_ENV=my_env"
rem set "CHATGPT2API_CMD=python -u main.py"
rem If your ChatGPT2API project uses uv, you can use:
rem set "CHATGPT2API_CMD=uv run main.py"
set "CHATGPT2API_DIR=F:\CodeProject\chatgpt2api"
set "CHATGPT2API_CONDA_ENV=zimg"
set "CHATGPT2API_CMD=python -m uvicorn main:app --host 127.0.0.1 --port 8010 --no-access-log --log-level info"

set "BACKEND_DIR=%~dp0backend_v2"
set "FRONTEND_DIR=%~dp0frontend_v2"

if defined CLIPROXY_DIR (
    if exist "%CLIPROXY_DIR%\%CLIPROXY_EXE%" (
        echo Starting CLIProxyAPI...
        start "CLIProxyAPI" cmd /k "cd /d ""%CLIPROXY_DIR%"" && %CLIPROXY_EXE%"
    ) else if exist "%CLIPROXY_DIR%\cmd\server\main.go" (
        echo Starting CLIProxyAPI...
        start "CLIProxyAPI" cmd /k "cd /d ""%CLIPROXY_DIR%"" && %CLIPROXY_CMD%"
    ) else (
        echo CLIProxyAPI path not found, skipped: %CLIPROXY_DIR%
    )
) else (
    echo CLIProxyAPI path is not configured, skipped.
)

if defined CHATGPT2API_DIR (
    if exist "%CHATGPT2API_DIR%" (
        echo Starting ChatGPT2API...
        if defined CHATGPT2API_CONDA_ENV (
            start "ChatGPT2API" cmd /k "cd /d ""%CHATGPT2API_DIR%"" && call conda activate %CHATGPT2API_CONDA_ENV% && %CHATGPT2API_CMD%"
        ) else (
            start "ChatGPT2API" cmd /k "cd /d ""%CHATGPT2API_DIR%"" && %CHATGPT2API_CMD%"
        )
    ) else (
        echo ChatGPT2API path not found, skipped: %CHATGPT2API_DIR%
    )
) else (
    echo ChatGPT2API path is not configured, skipped.
)

timeout /t 3 /nobreak >nul

echo Starting ProxyCanvas backend...
if defined BACKEND_CONDA_ENV (
    start "ProxyCanvas Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && call conda activate %BACKEND_CONDA_ENV% && %BACKEND_CMD%"
) else (
    start "ProxyCanvas Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && %BACKEND_CMD%"
)

timeout /t 3 /nobreak >nul

echo Starting ProxyCanvas frontend...
start "ProxyCanvas Frontend" cmd /k "cd /d ""%FRONTEND_DIR%"" && npm run dev"

timeout /t 5 /nobreak >nul

echo Opening ProxyCanvas...
start "" "http://localhost:5380"

echo.
echo ProxyCanvas started.
echo Frontend: http://localhost:5380
echo Backend:  http://localhost:5700
echo.
pause
