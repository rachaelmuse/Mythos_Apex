@echo off
REM Point Mythos heavy brain at Colibri low-RAM serve
set MYTHOS_HEAVY_MODEL=glm-5.2-colibri
set MYTHOS_HEAVY_API=http://127.0.0.1:8010/v1
set OPENAI_BASE_URL=http://127.0.0.1:8010/v1
set COLI_MODEL=D:\glm52_i4
set MYTHOS_ALLOW_HEAVY=1
echo Heavy brain -> Colibri :8010 (low-RAM + VRAM profile). Daily chat stays Ollama.
