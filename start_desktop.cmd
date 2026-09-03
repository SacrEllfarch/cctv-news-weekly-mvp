@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目虚拟环境，正在创建并安装桌面依赖...
  python -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败，请安装 Python 3.11+ 后重试。
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements-desktop.txt
)
start "" ".venv\Scripts\pythonw.exe" desktop_app.py
