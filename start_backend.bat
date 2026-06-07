@echo off
echo ================================
echo  AI达人猎手 - 启动后端服务
echo ================================
cd /d "%~dp0backend"
echo 正在启动 Flask API 服务 (端口 5000)...
"C:\Users\yunha\.workbuddy\binaries\python\envs\ai-hunter\Scripts\python.exe" app.py
pause
