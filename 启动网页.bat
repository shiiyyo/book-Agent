@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在启动「自动化写书」（端口 5000）...
echo 约 5 秒后会自动打开浏览器；若本窗口出现报错，请根据提示处理。
echo.
echo [前端样式已更新]
echo   - 书卷气无衬线字体、侧边栏浅灰背景与按钮圆角悬停
echo   - 顶部 KPI 仪表盘：当前书名、已写字数、逻辑健康度、累计耗时
echo   - 四栏 Tab：创作控制台 / 内容看板 / 学术人设库 / 系统设定
echo   - 流式正文纸张质感容器 + 进度条
echo   - 学术深蓝 / 小说橙色主题色，侧边栏可开关「宽屏模式」
echo   （Streamlit 控制台请另运行: streamlit run main.py）
echo.
rem 先预约 5 秒后打开浏览器（不阻塞）
start /b cmd /c "timeout /t 5 /nobreak >nul && start http://127.0.0.1:5000"
rem 优先用 PATH 里的 python；其次用 Windows 的 py 启动器；再自动找 Anaconda/Miniconda/常用路径
python api_server.py 2>nul
if not errorlevel 1 goto :end
py -3 api_server.py 2>nul
if not errorlevel 1 goto :end
echo 未在 PATH 中找到 Python，正在查找 Anaconda / Miniconda 及常用安装路径 ...
set "PYTHON_EXE="
if not defined PYTHON_EXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON_EXE if exist "C:\ProgramData\anaconda3\python.exe" set "PYTHON_EXE=C:\ProgramData\anaconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\AppData\Local\Continuum\anaconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\AppData\Local\Continuum\anaconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE if exist "C:\Python313\python.exe" set "PYTHON_EXE=C:\Python313\python.exe"
if not defined PYTHON_EXE if exist "C:\Python312\python.exe" set "PYTHON_EXE=C:\Python312\python.exe"
if defined PYTHON_EXE (
  echo 使用: %PYTHON_EXE%
  "%PYTHON_EXE%" api_server.py
) else (
  echo.
  echo 未找到 Python。请任选其一：
  echo   1. 若已安装 Anaconda：用「Anaconda Prompt」进入本目录后执行: python api_server.py
  echo   2. 若已安装 Python：将 Python 加入系统 PATH，或在本目录用「py -3 api_server.py」试一次
  echo   3. 若未安装：请先安装 Python 或 Anaconda 后再运行本脚本
  echo.
  pause
  exit /b 1
)
:end
echo.
echo 服务已退出。按任意键关闭窗口...
pause
