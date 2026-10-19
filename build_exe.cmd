@echo off
chcp 65001 >nul
rem 重新打包「模型管理器.exe」——改完 model_manager.pyw 后双击本文件即可
cd /d "C:\Users\DELL\.local\bin"

"C:\Python313\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
  --name model_manager ^
  --icon "C:\Users\DELL\.local\bin\mm.ico" ^
  --distpath "C:\Users\DELL\.local\bin\dist" ^
  --workpath "C:\Users\DELL\.local\bin\build_tmp" ^
  --specpath "C:\Users\DELL\.local\bin\build_tmp" ^
  "C:\Users\DELL\.local\bin\model_manager.pyw"

if errorlevel 1 (
    echo.
    echo [失败] 打包出错，看上面的输出
    pause
    exit /b 1
)

copy /y "C:\Users\DELL\.local\bin\dist\model_manager.exe" "C:\Users\DELL\Desktop\模型管理器.exe" >nul
echo.
echo [完成] 已更新桌面「模型管理器.exe」
pause
