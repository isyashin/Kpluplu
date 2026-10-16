@echo off
rem ЮрАгент: opencode только с cons++ MCP и локальными навыками
set OPENCODE_DISABLE_EXTERNAL_SKILLS=1
set OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1
cd /d "%~dp0"
opencode %*
