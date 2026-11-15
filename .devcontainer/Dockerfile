# Reachy Mini ワークショップ用の devcontainer イメージ
# GStreamer + WebRTC プラグイン（gst-plugins-rs）と Python 環境を焼き込み、
# Codespaces の起動時にビルドしなくて済むようにする。

ARG UBUNTU_IMAGE=ubuntu:24.04
# Codespaces 用の一般ユーザー（vscode）入りの Ubuntu 24.04
ARG DEVCONTAINER_IMAGE=mcr.microsoft.com/devcontainers/base:noble

# ---- WebRTC プラグインのビルド（Rust）-------------------------------------
FROM ${UBUNTU_IMAGE} AS gst-plugins-rs

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git pkg-config libssl-dev \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer-plugins-bad1.0-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.cargo/bin:$PATH
RUN cargo install cargo-c --locked

# 0.14.5 は webrtcsink のデッドロック修正を含む（SDK の GStreamer 手順に合わせる）
ARG GST_PLUGINS_RS_VERSION=0.14.5
RUN git clone --depth 1 --branch ${GST_PLUGINS_RS_VERSION} \
        https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git /src
WORKDIR /src
# libdir を固定し、アーキテクチャ（x86_64 / aarch64）に依存しないパスにする
RUN cargo cinstall -p gst-plugin-webrtc --release \
        --prefix=/opt/gst-plugins-rs --libdir=/opt/gst-plugins-rs/lib

# ---- 実行環境 --------------------------------------------------------------
FROM ${DEVCONTAINER_IMAGE}

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl git git-lfs sudo \
        build-essential pkg-config libgirepository1.0-dev libcairo2-dev \
        gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad gstreamer1.0-nice gstreamer1.0-alsa gstreamer1.0-pulseaudio \
        gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 \
        libnice10 libssl3 libportaudio2 alsa-utils libasound2-plugins \
        libegl1 libgl1 libgl1-mesa-dri libglfw3 xvfb xauth \
        pulseaudio \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gst-plugins-rs /opt/gst-plugins-rs /opt/gst-plugins-rs
ENV GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib

# Python 3.12 の仮想環境に SDK と追加パッケージを入れる
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/
# ベースイメージのシステム Python は開発用ヘッダーがなく PyGObject をビルドできないため、uv 管理の Python を使う
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN uv venv /opt/venv --seed --python 3.12

# reachy_mini サブモジュールのタグと揃える
ARG REACHY_MINI_VERSION=1.14.0
RUN uv pip install "reachy-mini[mujoco]==${REACHY_MINI_VERSION}" \
        soundfile python-dotenv openai pillow ipykernel aiohttp \
    && uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && chown -R vscode:vscode /opt/venv

# デーモンは既定で --headless で動かし、カメラ映像はビューア（viewer/media_bridge.py）が
# ブラウザから受け取って UDP 5005 に流す。シミュレータのカメラを使う設定（REACHY_MINI_CAMERA=sim）
# では、--headless だとカメラが動かないため Xvfb 上でビューア付きで動かす。描画は Mesa の llvmpipe。
