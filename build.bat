@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ========================================
echo KeyProbe - 打包脚本
echo ========================================
echo.

echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.12+
    pause
    exit /b 1
)
for /f "delims=" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [OK] !PYTHON_VERSION!

echo.
echo [2/5] 检查依赖...
python -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
    echo [WARN] 缺少依赖，正在安装...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败
        pause
        exit /b 1
    )
)
echo [OK] 依赖检查通过

echo.
echo [3/5] 清理旧的构建文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo [OK] 清理完成

echo.
echo [4/5] 开始打包...
pyinstaller --onefile --windowed --name "KeyProbe" --add-data "config.json;." --distpath "./dist" --workpath "./build" --specpath "./build" main.py

echo.
echo [5/5] 检查打包结果...
if exist "dist\KeyProbe.exe" (
    echo ========================================
    echo 打包成功
    echo 路径 "dist\KeyProbe.exe"
    echo ========================================
) else (
    echo ========================================
    echo 打包失败
    echo ========================================
)
echo.
pause
