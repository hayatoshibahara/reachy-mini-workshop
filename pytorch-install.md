# Jetson Orin Nano 向け PyTorch CUDA 環境の導入手順

この手順は、`test_stt_wav.py` や `speech-to-speech` の Whisper STT を Jetson Orin Nano の GPU で動かすためのメモです。

現在の `.reachy_mini_local_conversation` では、PyTorch が CUDA 13.0 ビルドです。

```text
torch 2.12.1+cu130
torch.version.cuda = 13.0
torch.cuda.is_available() = False
```

一方、Jetson 側のドライバは CUDA 12.6 系として見えているため、`torch.cuda.is_available()` が `False` になります。Jetson ではデスクトップGPUのようにドライバだけを更新するのではなく、JetPack / L4T に合った PyTorch を使います。

基本方針:

- bare-metal の venv で使うなら、CUDA 12.6 / JetPack 6.2 対応の `linux_aarch64` PyTorch を使う
- generic PyPI の `torch` や CUDA 13.0 wheel は使わない
- NVIDIA公式の JetPack 6.2 互換表では standalone wheel が `-` なので、公式サポートを重視するなら NVIDIA PyTorch コンテナを使う

## 1. 現在のJetsonバージョンを確認する

```bash
cat /etc/nv_tegra_release
```

この環境では以下でした。

```text
# R36 (release), REVISION: 4.7, ...
```

R36.4.x は JetPack 6.2 系として扱います。NVIDIA の PyTorch for Jetson 互換表では、JetPack 6.2 向けは以下です。

```text
PyTorch: 2.8.0a0+5228986c39
NVIDIA Framework Container: 25.06 または 25.05
NVIDIA Framework Wheel: -
JetPack: 6.2
```

つまり JetPack 6.2 では、NVIDIA 公式表上は standalone wheel ではなく NVIDIA PyTorch コンテナを使うのが安全です。

注意: NVIDIA PyTorch コンテナのリリースノートでは、25.05 / 25.06 コンテナ内の CUDA Toolkit は 12.9 系と記載されています。一方で、PyTorch for Jetson Platform の互換表では JetPack 6.2 対応として 25.05 / 25.06 が示されています。bare-metal の venv に直接入れる場合は、ホストの JetPack 6.2 / CUDA 12.6 に合う aarch64 wheel を選ぶ必要があります。

## 2. 推奨: NVIDIA PyTorch コンテナを使う

JetPack 6.2 系では、まずこの方法を使います。

### 2.1 Docker / NVIDIA runtime を確認する

```bash
docker version
docker info | grep -i runtime
```

Docker 19.03 以降では `--gpus all` が使えます。Jetson の設定によっては `--runtime nvidia` が必要な場合があります。

### 2.2 PyTorch 25.06 コンテナを取得する

```bash
docker pull nvcr.io/nvidia/pytorch:25.06-py3
```

### 2.3 ワークショップディレクトリをマウントして起動する

```bash
cd /home/engineercafejp/reachy-mini-workshop

docker run --gpus all --rm -it \
  --network host \
  --shm-size=1g \
  -v "$PWD":/workspace/reachy-mini-workshop \
  -v "$HOME/.cache/huggingface":/root/.cache/huggingface \
  nvcr.io/nvidia/pytorch:25.06-py3
```

もし `--gpus all` が Jetson 側で動かない場合は、次を試します。

```bash
docker run --runtime nvidia --rm -it \
  --network host \
  --shm-size=1g \
  -v "$PWD":/workspace/reachy-mini-workshop \
  -v "$HOME/.cache/huggingface":/root/.cache/huggingface \
  nvcr.io/nvidia/pytorch:25.06-py3
```

### 2.4 コンテナ内でCUDA確認

コンテナ内で実行します。

```bash
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("torch cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
PY
```

期待値:

```text
cuda available: True
device: Orin
```

デバイス名は環境により多少異なります。

## 3. コンテナ内で speech-to-speech を使う

コンテナに入ったあと、ワークショップへ移動します。

```bash
cd /workspace/reachy-mini-workshop
```

必要な Python 依存関係を入れます。ここで PyPI の generic `torch` に置き換えないことが重要です。

```bash
python -m pip install -U pip setuptools wheel
python -m pip install "numpy==1.26.4"
python -m pip install -e "speech-to-speech[kokoro]"
```

インストール後、PyTorch が NVIDIA コンテナ内の CUDA 対応版のままか確認します。

```bash
python - <<'PY'
import torch
print(torch.__version__)
print(torch.version.cuda)
print(torch.cuda.is_available())
PY
```

`False` になる場合は、その環境で PyTorch が generic wheel に置き換わっています。コンテナを作り直して、`torch` を個別に `pip install -U torch` しないでください。

## 4. STTレイテンシ確認

VAD出力ファイルを作成済みなら、コンテナ内で以下を実行します。

```bash
python test_stt_wav.py
```

`test_stt_wav.py` のデフォルトは低レイテンシ向けです。

```text
model = openai/whisper-large-v3-turbo
language = ja
device = cuda
dtype = float16
runs = 3
real_audio_warmups = 1
```

GPUが使える場合は、`Latency summary` が表示されます。

```text
Latency summary: min=...s p50=...s mean=...s max=...s
Final transcription: 学校
```

`NvMapMemAllocInternalTagged: ... error 12` や `CUDACachingAllocator` の内部 assert が出る場合は、Whisper モデルを CUDA に載せる段階で Jetson の連続メモリ確保に失敗しています。`openai/whisper-large-v3-turbo` は Orin Nano では環境状態によって厳しいため、まず再起動して他のGPU/メモリ使用プロセスを止めた状態で再試行します。

それでも失敗する場合は、STT単体の疎通確認を小さいモデルで行います。

```bash
python test_stt_wav.py --model-name openai/whisper-small --no-warmup --real-audio-warmups 0 --runs 1
```

フルデモでも同じように `--stt_model_name openai/whisper-small` へ落とすか、量子化された `faster-whisper` 系の利用を検討してください。

## 5. venvに直接入れる場合の注意

この環境の現在の venv は Python 3.12 です。

```bash
source .reachy_mini_local_conversation/bin/activate
python --version
```

NVIDIA Jetson 向け wheel は JetPack の system Python を前提にしたものが多く、Python 3.10 向け wheel しかない場合があります。そのため、Jetson GPU 用に venv を作る場合は、まず system Python 3.10 の venv を検討してください。

```bash
python3 --version
python3 -m venv .reachy_mini_local_conversation_cuda
source .reachy_mini_local_conversation_cuda/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install "numpy==1.26.4"
```

そのうえで、NVIDIA の PyTorch for Jetson 互換表から、JetPack の版に合う wheel URL を選びます。

```bash
export TORCH_INSTALL="<NVIDIA互換表に記載されたJetPack対応wheelのURL>"
python -m pip install --no-cache-dir "$TORCH_INSTALL"
```

ただし、JetPack 6.2 の互換表では standalone wheel が `-` です。R36.4.x / JetPack 6.2 系では、venv直入れよりコンテナ方式を優先してください。

## 6. bare-metal で CUDA 12.6 PyTorch を使う場合

コンテナではなく現在のワークショップ環境に近い形で動かしたい場合は、CUDA 12.6 / JetPack 6.2 対応の `linux_aarch64` wheel が必要です。

重要なのは、次の条件をすべて満たす wheel を使うことです。

```text
OS / CPU: linux_aarch64
JetPack: 6.2 / L4T R36.4.x
CUDA: 12.6 系
Python ABI: 使用するPythonと一致すること。例: cp310, cp312
```

現在の venv は Python 3.12 なので、wheel ファイル名に `cp312` と `linux_aarch64` が含まれるものが必要です。`cp310` wheel は Python 3.12 venv には入りません。

### 6.1 CUDA 12.6 wheel を探すときの見方

wheel URL またはファイル名は、少なくとも次のような条件を満たす必要があります。

```text
torch-...+...-cp312-cp312-linux_aarch64.whl
```

Python 3.10 の venv を使う場合は以下です。

```text
torch-...+...-cp310-cp310-linux_aarch64.whl
```

CUDA 12.6 対応であることは、配布元の説明で JetPack 6.2 / CUDA 12.6 対応と明記されていることを確認します。ファイル名だけでは CUDA 対応版か判断しきれない場合があります。

### 6.2 インストール手順

まず今の CUDA 13.0 版 PyTorch を外します。

```bash
source .reachy_mini_local_conversation/bin/activate
python -m pip uninstall -y torch torchvision torchaudio
```

次に、JetPack 6.2 / CUDA 12.6 対応の aarch64 wheel を入れます。

```bash
export TORCH_WHEEL="<JetPack 6.2 / CUDA 12.6 / linux_aarch64 / Python ABI一致のtorch wheel URL>"
python -m pip install --no-cache-dir "$TORCH_WHEEL"
```

`torchvision` や `torchaudio` が必要な場合も、同じ配布元・同じJetPack・同じPython ABIのものを入れます。x86_64向けの wheel を混ぜないでください。

### 6.3 speech-to-speech 再インストール時の注意

`speech-to-speech` は依存関係として `torch>=2.4.0` を持っています。そのため、何も指定せずに再インストールすると、pip が再び generic PyPI の `torch` を入れてしまう可能性があります。

PyTorch を入れた後に `speech-to-speech` を入れ直す場合は、PyTorch を上書きしないようにします。

```bash
python -m pip install -e "speech-to-speech[kokoro]" --no-deps
```

不足している依存関係は個別に入れます。`torch` を再インストールしないことを優先してください。

Jetson向けPyTorch wheelでは、NumPy 2.x と組み合わせると `RuntimeError: Numpy is not available` が出る場合があります。Whisper の feature extractor は `torch.from_numpy()` を使うため、この環境では NumPy 1.x に固定します。

```bash
python -m pip install --force-reinstall "numpy==1.26.4"
```

確認:

```bash
python - <<'PY'
import numpy as np
import torch
print("numpy:", np.__version__)
print("torch:", torch.__version__)
print("torch cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
PY
```

期待値:

```text
torch cuda: 12.6 付近
cuda available: True
```

## 7. やってはいけないこと

Jetson 上で次のような generic PyPI CUDA wheel に置き換えないでください。

```bash
python -m pip install -U torch torchvision torchaudio
```

また、PyTorch公式のCUDA 12.6 indexをそのまま使う方法にも注意してください。

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cu126
```

この方法は、Jetson の `linux_aarch64` 向け wheel が存在しない、または JetPack と合わない wheel を選ぶ可能性があります。Jetson では、`cu126` であることに加えて `linux_aarch64` / JetPack対応であることが必須です。

今回の問題は、これに近い状態で `torch 2.12.1+cu130` が入り、Jetson の CUDA 12.6 系ドライバと合わなくなったことです。

また、Jetson では次の警告に従ってデスクトップGPU向けドライバを手動更新しないでください。

```text
The NVIDIA driver on your system is too old...
```

Jetson のドライバは JetPack / L4T と一体です。PyTorch 側を JetPack に合わせます。

## 8. 参照元

- NVIDIA: Installing PyTorch for Jetson Platform  
  https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html
- NVIDIA: PyTorch for Jetson Platform Release Notes  
  https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform-release-notes/pytorch-jetson-rel.html
- NVIDIA: PyTorch Release 25.06  
  https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-25-06.html
- NVIDIA: NGC container / GPU enabled Docker usage  
  https://docs.nvidia.com/deeplearning/frameworks/user-guide/index.html#enable-gpu-support
