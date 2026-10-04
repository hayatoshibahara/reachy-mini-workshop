# スクリプト版

`main.ipynb` を Jupyter Notebook なしで検証するための Python スクリプト集です。
前提環境（Git / Git LFS / uv）のインストールは `main.ipynb` を参照してください。

## インストール

リポジトリ直下で実行します。

```sh
# 仮想環境を作成して有効化
uv venv .reachy_mini_env --seed --python 3.12
source .reachy_mini_env/bin/activate        # 🍎 macOS
.reachy_mini_env\Scripts\activate           # 🪟 Windows

# Reachy Mini SDK v1.10.0 を取得してインストール
git clone https://github.com/pollen-robotics/reachy_mini.git
git -C reachy_mini checkout v1.10.0
uv pip install -e "./reachy_mini[mujoco]"

# スクリプトが使う追加パッケージ
uv pip install soundfile matplotlib python-dotenv openai scipy torch torchaudio pillow
```

OpenAI API を使うスクリプト（`07`〜`09`、`11`〜`13`）のために、リポジトリ直下に `.env` を作成します。

```sh
OPENAI_API_KEY=sk-proj-xxxxxxxx
```

## 実行

### 1. シミュレータを起動

別のターミナルで仮想環境を有効化し、起動したままにします（`Ctrl + c` で停止）。

```sh
# 🍎 macOS
mjpython -m reachy_mini.daemon.app.main --sim --scene minimal

# 🪟 Windows
reachy-mini-daemon --sim --scene minimal
```

### 2. スクリプトを実行

リポジトリ直下から、仮想環境を有効化した状態で実行します。

```sh
python script/01_move.py
```

| スクリプト | 内容 | 備考 |
|---|---|---|
| `01_move.py` | 頭とアンテナを動かす | |
| `02_gstreamer.py` | GStreamer の初期化とプラグイン確認 | |
| `03_devices.py` | デバイス一覧、音量設定、カメラ仕様 | |
| `04_record.py` | 5 秒録音して検証し、`recorded.wav` に保存 | |
| `05_play.py` | `recorded.wav` をスピーカーで再生 | 先に `04` |
| `06_camera.py` | カメラで 1 枚撮影して表示 | |
| `07_transcribe.py` | `recorded.wav` を文字起こし | 先に `04`、API キー |
| `08_response.py [テキスト]` | 文字起こしして回答を生成 | 先に `04`（テキスト指定なら不要）、API キー |
| `09_tts.py [テキスト]` | 音声を生成して再生 | API キー |
| `10_vad.py` | `recorded.wav` から発話区間を抽出 | 先に `04` |
| `11_camera_tool.py [質問]` | カメラを使う Function Calling | API キー |
| `12_move_tool.py [指示]` | ダンス・感情の動きを使う Function Calling | API キー |
| `13_conversation.py` | 会話デモ（3 ターン、各 4 秒録音） | API キー |

`common.py` は各スクリプトが共有するヘルパーで、単体では実行しません。

## トラブルシューティング

- 録音に失敗する場合（macOS）: 「システム設定 → プライバシーとセキュリティ → マイク」で、使用中のターミナルまたはエディタをオフ → オンにして再起動します。
- `(.reachy_mini_env) (base)` のように環境が二重に表示される場合: `conda deactivate` を実行します。
- 生成される `.wav` はリポジトリ直下に出力され、`.gitignore` の対象です。

## Jetson Orin Nano でのインストール

Jetson（JetPack 6 / Ubuntu 22.04 / aarch64）は Linux 扱いのため、macOS・Windows と違って GStreamer が同梱されず、手動の追加対応が必要です。以下は [Reachy Mini SDK の GStreamer インストール手順](https://github.com/pollen-robotics/reachy_mini/blob/v1.10.0/docs/source/SDK/gstreamer-installation.md)を Jetson 向けに読み替えたもので、Jetson 実機での動作確認はしていません。

### 0. 現在の GStreamer を確認する

再インストールが必要かを、先に次のコマンドで判断します。

```sh
# OS・アーキテクチャ・JetPack（Ubuntu 22.04 / aarch64 が想定）
cat /etc/os-release | head -2
uname -m
cat /etc/nv_tegra_release      # JetPack(L4T) のバージョン

# GStreamer のバージョン（1.24 以上が必要）
gst-launch-1.0 --version
pkg-config --modversion gstreamer-1.0

# 必要なプラグインの有無（見つからなければ "No such element" と出る）
for p in unixfdsink unixfdsrc webrtcsink webrtcbin webrtcdsp webrtcechoprobe \
         equalizer-10bands audiodynamic appsink appsrc jpegdec; do
  gst-inspect-1.0 "$p" > /dev/null 2>&1 && echo "✅ $p" || echo "❌ $p"
done

# Python から gi を読み込めるか（仮想環境を有効化した状態で）
python -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None); print(Gst.version_string())"
```

判断の目安は次のとおりです。

- バージョンが 1.24 以上で、すべて ✅ なら、1〜3 は不要です。
- バージョンが 1.24 未満（JetPack 6 の標準は 1.20 系）なら、手順 1 から実施します。`unixfdsink` / `unixfdsrc` は 1.24 で追加されたため、❌ になります。
- バージョンは足りていても `webrtcsink` / `webrtcbin` が ❌ なら、手順 2 から実施します。
- プラグインはすべて ✅ でも Python の `gi` でエラーになるなら、手順 3 だけ実施します。

### 1. GStreamer を 1.24 以上にする

```sh
sudo apt-get update
sudo apt-get install \
    libgstreamer-plugins-bad1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer1.0-dev \
    libglib2.0-dev libssl-dev libgirepository1.0-dev libcairo2-dev libportaudio2 libnice10 \
    gstreamer1.0-plugins-good gstreamer1.0-alsa gstreamer1.0-plugins-bad gstreamer1.0-nice \
    gstreamer1.0-tools

# Ubuntu 22.04 は標準が古いため PPA で更新
sudo add-apt-repository ppa:savoury1/multimedia
sudo apt update
sudo apt install \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-good1.0-dev libgstreamer-plugins-bad1.0-dev \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad

pkg-config --modversion gstreamer-1.0   # 1.24.x 以上であること
```

### 2. WebRTC プラグインをビルド

Linux では `webrtcsink` / `webrtcbin` 系のプラグインが標準で入らないため、Rust でビルドします（Jetson Orin Nano では数十分かかることがあります）。

```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git
cd gst-plugins-rs
git checkout 0.14.5          # webrtcsink のデッドロック修正を含む版
cargo install cargo-c

sudo mkdir -p /opt/gst-plugins-rs && sudo chown $USER /opt/gst-plugins-rs
cargo cinstall -p gst-plugin-webrtc --prefix=/opt/gst-plugins-rs --release

# Jetson は aarch64（公式手順の x86_64-linux-gnu ではない）
echo 'export GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib/aarch64-linux-gnu:$GST_PLUGIN_PATH' >> ~/.bashrc
source ~/.bashrc

gst-inspect-1.0 webrtcsink   # プラグインが見つかること
```

### 3. Python 側（`gi`）

SDK は Linux で `PyGObject` を pip でビルドして使います。JetPack 標準の `python3-gi` は Python 3.10 用なので、Python 3.12 の仮想環境からは使えません。手順 1 の `libgirepository1.0-dev` と `libcairo2-dev` を入れたうえで、`uv pip install -e "./reachy_mini[mujoco]"` の中でビルドさせます。

インストール後は `python script/02_gstreamer.py` で、全プラグインが ✅ になることを確認します。

### 4. その他の注意

- **`mjpython` は使えない:** `mjpython` は macOS 専用です。Linux ではシミュレータを `reachy-mini-daemon --sim --scene minimal` で起動します。MuJoCo の描画に OpenGL が必要なため、ディスプレイを接続するか、ヘッドレスなら `MUJOCO_GL=egl` を設定します。
- **音声・カメラ:** Jetson にはオンボードのマイクとスピーカーが無いので、USB マイク・スピーカー、またはカメラを接続し、`03_devices.py` で認識されているか確認します。シミュレータ上のカメラは MuJoCo UDP 経由です。
- **torch:** `10_vad.py` と `13_conversation.py` が使う Silero VAD は CPU で十分に動きます。PyPI の aarch64 版 `torch` は CUDA なしの CPU 版になります。GPU を使いたい場合は、JetPack 対応の NVIDIA 提供ホイールを別途入れてください。
- **メモリ:** Orin Nano（4GB / 8GB）では、`cargo cinstall` や `torch` のインストール中にメモリ不足になることがあります。その場合はスワップを追加するか、`cargo cinstall` に `-j 2` を付けてください。
