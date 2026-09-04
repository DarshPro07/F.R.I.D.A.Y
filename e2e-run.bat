@echo off
set PATH=D:\software;%PATH%
set PLAYWRIGHT_BROWSERS_PATH=D:\playwright-browsers
node node_modules\@playwright\test\cli.js test %* --project=chromium --reporter=list > e2e-run.log 2>&1
echo EXIT=%ERRORLEVEL% >> e2e-run.log
