@echo off
rem Install the OpenChorus AI service: quality judge (audiobox-aesthetics), lyric clarity
rem (faster-whisper), cover art (SSD-1B + LCM-LoRA) and 4-stem separation (Demucs).
rem Uses an NVIDIA GPU when there is one, else the CPU (slower). About 8 GB of downloads the first time.
cd /d "%~dp0ai_service"
where uv >nul 2>nul || powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if not exist .venv uv venv --python 3.11 .venv || exit /b 1
where nvidia-smi >nul 2>nul && (
  echo NVIDIA GPU found: installing PyTorch with CUDA
  uv pip install --python .venv\Scripts\python.exe torch==2.7.1+cu128 torchaudio==2.7.1+cu128 --index-url https://download.pytorch.org/whl/cu128 || exit /b 1
) || (
  echo No NVIDIA GPU: installing the CPU build of PyTorch
  uv pip install --python .venv\Scripts\python.exe torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cpu || exit /b 1
)
uv pip install --python .venv\Scripts\python.exe -r requirements.txt || exit /b 1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download as s; s('segmind/SSD-1B', allow_patterns=['*.json','*.txt','*fp16.safetensors','tokenizer*/*','tokenizer_2/*']); s('latent-consistency/lcm-lora-ssd-1b'); s('facebook/audiobox-aesthetics'); s('mobiuslabsgmbh/faster-whisper-large-v3-turbo'); print('Models ready')"
echo Done. start_all.bat now starts the AI service on port 8002.
