@echo off
REM Gated cloud heavy brain for Mythos (OpenAI-compatible).
REM Daily chat stays on Ollama. Only brain.heavy / escalate / agent.loop use this.
REM
REM Usage: edit the three set lines below, then run this before START_CODEX.bat
REM   set MYTHOS_ALLOW_CLOUD_HEAVY=1
REM   set MYTHOS_HEAVY_CLOUD_API=https://api.openai.com/v1
REM   set MYTHOS_HEAVY_CLOUD_KEY=sk-...
REM   set MYTHOS_HEAVY_CLOUD_MODEL=gpt-4o-mini

set MYTHOS_ALLOW_CLOUD_HEAVY=1
if "%MYTHOS_HEAVY_CLOUD_API%"=="" set MYTHOS_HEAVY_CLOUD_API=https://api.openai.com/v1
if "%MYTHOS_HEAVY_CLOUD_MODEL%"=="" set MYTHOS_HEAVY_CLOUD_MODEL=gpt-4o-mini
REM set MYTHOS_HEAVY_CLOUD_KEY=sk-your-key-here

echo Cloud heavy enabled: ALLOW=%MYTHOS_ALLOW_CLOUD_HEAVY% API=%MYTHOS_HEAVY_CLOUD_API% MODEL=%MYTHOS_HEAVY_CLOUD_MODEL%
echo Set MYTHOS_HEAVY_CLOUD_KEY in this shell (or system env) before starting Codex.
