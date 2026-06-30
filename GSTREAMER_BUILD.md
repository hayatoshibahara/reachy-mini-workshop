# GStreamer のビルドとインストール手順（Jetson / Reachy Mini）

Reachy Mini の SDK は、カメラ映像とマイク音声を **WebRTC** で配信するために
**GStreamer 1.24 以上**（WebRTC プラグイン有効）を必要とします。
しかし Jetson（JetPack 6 / L4T 36.4.x、Ubuntu 22.04 ベース）が標準で持つ GStreamer は
**1.20** と古く、`apt` の PPA も arm64 向けには 1.24 を配布していません。
そのため **ソースから 1.24.x をビルドして `/usr/local` に導入** します。

この手順は実機（aarch64 / Jetson）で検証済みの構成に基づいています。

| 項目 | 値 |
|---|---|
| GStreamer | 1.24.13（ソースビルド、`/usr/local`） |
| gst-plugins-rs | 0.14.5（WebRTC プラグイン、`/opt/gst-plugins-rs`） |
| アーキテクチャ | `aarch64-linux-gnu` |
| ビルド | meson + ninja（`buildtype=debugoptimized`） |

> **重要な注意点（このリポジトリで実際にハマった箇所）**
> 1. **`libjpeg-dev` を入れずにビルドすると `jpegdec` が作られず、USB カメラの MJPEG をデコードできません。** 必ず依存パッケージを先に入れてください。
> 2. **1.20 の NVIDIA / システムプラグインは 1.24 コアと ABI 非互換で、`undefined symbol` 警告やプロセスの `stack smashing detected`（クラッシュ）を引き起こします。** 手順 5 で無効化します。
> 3. **カメラが検出されない場合は、手順 6（PipeWire プラグイン）未実施か、ノートブックでの `GI_TYPELIB_PATH` 未設定が原因です。** 手順 6・手順 7 を参照してください。

---

## 1. 前提

- Jetson（JetPack 6 / L4T 36.4.x、Ubuntu 22.04, arm64）
- `sudo` 権限
- インターネット接続（数百 MB のソース取得・ビルドに時間がかかります）

> **uv venv（`.reachy_mini_env` など）を使っている場合：ビルド時は venv を有効化しないでください。**
> GStreamer のビルドは `/usr/local` に入る**システム規模の C/C++ ビルド**で、特定の Python
> 環境に紐づくものではありません。むしろ venv を有効化すると次の不都合があります。
> - meson は Python で動作し、`gobject-introspection`（typelib 生成）にも Python を使います。
>   venv が有効だと **venv 側の Python** が拾われ、検出結果が変わることがあります。
> - `meson` は**システムの `python3`** に入っています。venv 内には無い、または別バージョンの
>   可能性があります。
>
> ビルド前に venv を抜けて、システムのツールを使っていることを確認します。
>
> ```bash
> deactivate            # venv が有効なら（または新しいターミナルを開く）
> which meson python3   # /usr/bin/... であること（.venv のパスでないこと）
> ```
>
> **venv が関係するのは「ビルド時」ではなく「実行時」だけ**です。Reachy の Python コードは
> `gi`（PyGObject）と `GI_TYPELIB_PATH` / `GST_PLUGIN_PATH` を通じて、ここでビルドした
> `/usr/local` の GStreamer を利用します（手順 7 を参照）。
> venv が正しく連携できているかは、次で確認できます（`GStreamer 1.24.13` と出れば OK）。
>
> ```bash
> .reachy_mini_env/bin/python -c \
>   "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; print(Gst.version_string())"
> ```
>
> `No module named 'gi'` となる場合は、venv に PyGObject が無いので、venv を
> `--system-site-packages` で作り直すか PyGObject を導入してください（GStreamer 本体の
> ビルドとは別の作業です）。

---

## 2. 依存パッケージのインストール

必要なプラグインが「無言でスキップ」されないよう、**ビルド前に**開発パッケージを揃えます。

```bash
sudo apt update
sudo apt install -y \
    git curl flex bison cmake pkg-config \
    ninja-build python3-pip \
    libglib2.0-dev \
    gobject-introspection libgirepository1.0-dev python3-gi python3-gi-cairo \
    libjpeg-dev \
    libv4l-dev libgudev-1.0-dev \
    libpulse-dev libasound2-dev \
    libvpx-dev libopus-dev \
    libnice-dev libsrtp2-dev libssl-dev
```

| パッケージ | 役割 / 対応プラグイン |
|---|---|
| `libjpeg-dev` | **`jpegdec`（USB カメラの MJPEG デコード）— 必須・忘れやすい** |
| `libgirepository1.0-dev`, `gobject-introspection` | `GI typelib`（ノートブックのデバイス検出に必須） |
| `libv4l-dev`, `libgudev-1.0-dev` | `v4l2src`（カメラ）, デバイス検出 |
| `libpulse-dev`, `libasound2-dev` | `pulsesrc` / `alsasrc`（音声） |
| `libvpx-dev`, `libopus-dev` | `vp8enc` / `opusenc`（WebRTC のソフトエンコード） |
| `libnice-dev`, `libsrtp2-dev`, `libssl-dev` | WebRTC のトランスポート（ICE / SRTP / DTLS） |

> **任意:** ソフトウェア H264 エンコード（`x264enc`）が必要なら
> `sudo apt install -y libx264-dev` を追加し、手順 3 の meson に `-Dgpl=enabled` を付けます。
> WebRTC は VP8（`vp8enc`）でも動作するため、必須ではありません。

meson は新しめのバージョンが必要です（古い場合は pip で）:

```bash
meson --version   # 1.1 以上であること。古ければ:  pip3 install --user --upgrade meson
```

---

## 3. GStreamer 1.24.13 のビルド

```bash
git clone -b 1.24.13 https://gitlab.freedesktop.org/gstreamer/gstreamer.git ~/gstreamer-src
cd ~/gstreamer-src

meson setup builddir \
    --prefix=/usr/local \
    --libdir=lib/aarch64-linux-gnu \
    -Dbuildtype=debugoptimized \
    -Dgood=enabled
    # H264 ソフトエンコードも欲しい場合は次を追加:  -Dgpl=enabled

ninja -C builddir
sudo ninja -C builddir install
sudo ldconfig
```

`/usr/local/lib/aarch64-linux-gnu` に 1.24 のライブラリ・プラグイン・typelib が入り、
`ldconfig` により 1.20 より優先されます。

---

## 4. WebRTC プラグイン（gst-plugins-rs）のビルド

WebRTC プラグインは Rust 製のため、別途ビルドします。

```bash
# Rust（未インストールの場合）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# cargo-c（C ABI 用のビルドツール）
cargo install cargo-c

# ソース取得（0.14.5 は webrtcsink のデッドロック修正を含む推奨タグ）
git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git ~/gst-plugins-rs
cd ~/gst-plugins-rs
git checkout 0.14.5

# インストール先を用意
sudo mkdir -p /opt/gst-plugins-rs
sudo chown "$USER" /opt/gst-plugins-rs

# ビルド & インストール（数分かかります）
cargo cinstall -p gst-plugin-webrtc --prefix=/opt/gst-plugins-rs --release
```

---

## 5. 競合する 1.20 プラグインの無効化（クラッシュ対策）

1.20 の NVIDIA / システムプラグインは 1.24 コアと ABI 非互換です。
`undefined symbol`（読み込み失敗の警告）や、`stack smashing detected`（プロセスの
クラッシュ）の原因になるため、プラグインディレクトリごと退避します（**可逆**）。

```bash
sudo mv /usr/lib/aarch64-linux-gnu/gstreamer-1.0 \
        /usr/lib/aarch64-linux-gnu/gstreamer-1.0.disabled-1.20

# 古いレジストリキャッシュを破棄
rm -rf ~/.cache/gstreamer-1.0/
```

> Reachy Mini のパイプラインは `v4l2src`（USB カメラ）+ `webrtcsink`（VP8/Opus、
> ソフトエンコード）のみを使い、NVIDIA のハードウェアプラグインは使用しません。
> したがってこの退避による機能的な影響はありません。
> NVIDIA のハードウェアプラグインは 1.20 専用のバイナリで、1.24 では利用できません
> （GStreamer を再ビルドしても入手できません）。
>
> 元に戻すには: `sudo mv …gstreamer-1.0.disabled-1.20 …gstreamer-1.0`

---

## 6. PipeWire GStreamer プラグインのビルド（カメラ検出に必須）

Reachy Mini のカメラ検出（`find_video_device`）は、デバイスの **`api.v4l2.path`**
プロパティでカメラを識別します。このプロパティを公開するのは GStreamer の
**PipeWire デバイスプロバイダ（`pipewiredeviceprovider`）** だけです。

手順 5 で 1.20 プラグインを無効化した際、このプロバイダを含む `libgstpipewire.so`
（apt の `gstreamer1.0-pipewire`）も一緒に無効化されました。ソースビルドした 1.24 には
PipeWire サポートが含まれないため、そのままでは **カメラが検出されず**、デーモンは
`No camera found` と表示し、`mini.media.get_frame()` は `None` を返します
（音声は別プロバイダを使うため影響を受けません）。

> ネイティブの `v4l2deviceprovider` でもカメラ自体は列挙されますが、パスを
> `device.path` として公開するため、`api.v4l2.path` を見る Reachy のコードには
> 一致しません。そのため PipeWire プロバイダが必要です。

PipeWire の GStreamer プラグインは **GStreamer 本体ではなく PipeWire のソースツリー**に
含まれます。実行中の PipeWire と ABI を一致させるため、**システムと同じバージョン**で
ビルドします（このマシンは `0.3.48`）。

```bash
# 実行中の PipeWire バージョンを確認し、同じタグをビルドする
pipewire --version          # 例: Linked with libpipewire 0.3.48

# ビルド依存（最小構成）
sudo apt install -y build-essential meson ninja-build libdbus-1-dev libudev-dev

# システムと同じバージョンのソースを取得（ABI を一致させる）
git clone --depth 1 --branch 0.3.48 \
    https://gitlab.freedesktop.org/pipewire/pipewire.git ~/pipewire-src
cd ~/pipewire-src

# GStreamer プラグイン + デバイスプロバイダだけを /usr/local の 1.24 に対して構成
PKG_CONFIG_PATH=/usr/local/lib/aarch64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig \
meson setup build \
    -Dgstreamer=enabled -Dgstreamer-device-provider=enabled \
    -Dexamples=disabled -Dtests=disabled -Dman=disabled -Ddocs=disabled \
    -Dpipewire-alsa=disabled -Dpipewire-jack=disabled -Dpipewire-v4l2=disabled \
    -Dalsa=disabled -Djack=disabled -Dbluez5=disabled -Dvulkan=disabled \
    -Dsession-managers='[]' -Dsystemd=disabled

# GStreamer プラグインのみビルド（デーモン等は作らない）
ninja -C build src/gst/libgstpipewire.so

# GST_PLUGIN_PATH 上のユーザ書き込み可能ディレクトリに配置（sudo 不要）
cp build/src/gst/libgstpipewire.so /opt/gst-plugins-rs/lib/aarch64-linux-gnu/

# レジストリ再構築
rm -rf ~/.cache/gstreamer-1.0/
```

> - プラグインは実行時に `libpipewire-0.3.so.0`（システムの 0.3.48）を読み込みます。
>   同じ 0.3.48 ソースからビルドすれば ABI が一致します。新しすぎるバージョンで
>   ビルドすると `undefined symbol` で読み込めません。
> - `meson setup` でオプション名がバージョン違いで弾かれる場合は、該当オプションを
>   外すか `meson configure build` で正しい名前を確認してください。

確認:

```bash
# プロバイダが登録されていること
# （device provider は bare name では gst-inspect に出ないので、プラグイン経由で確認）
gst-inspect-1.0 pipewire 2>/dev/null | grep pipewiredeviceprovider && echo "provider OK"

# カメラが api.v4l2.path 付きで検出されること（この行が出れば成功）
gst-device-monitor-1.0 Video/Source 2>/dev/null | grep -E "api\.v4l2\.path"
```

---

## 7. 環境変数の設定

### シェル / デーモン用（`~/.bashrc`）

```bash
# 1.24 のプラグイン（/usr/local、既定の探索パス）+ Rust WebRTC プラグイン
export GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib/aarch64-linux-gnu
# 1.24 の GI typelib を使う（デバイス検出に必須）
export GI_TYPELIB_PATH=/usr/local/lib/aarch64-linux-gnu/girepository-1.0
```

設定後 `source ~/.bashrc`（または新しいターミナル）を実行します。

> **`GST_PLUGIN_PATH` に `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/` を含めないこと。**
> これは手順 5 で無効化した 1.20 プラグインの場所で、クラッシュの原因になります。

### VSCode / Jupyter ノートブック用（`.env`）

VSCode の Jupyter カーネルは `~/.bashrc` を読み込みません。
ワークスペース直下の `.env` をカーネル起動時に読み込むため、ここに記載します。

```dotenv
GI_TYPELIB_PATH=/usr/local/lib/aarch64-linux-gnu/girepository-1.0
GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib/aarch64-linux-gnu
```

> 反映には **カーネルの再起動**（"Restart Kernel"）が必要です（セルの再実行だけでは不十分）。

---

## 8. 動作確認

```bash
# バージョン（1.24.13 と表示されること）
gst-launch-1.0 --version

# 読み込み失敗が無いこと（何も出力されなければ OK）
gst-inspect-1.0 2>&1 | grep -i "failed to load"

# 必要なプラグインが揃っていること（すべて情報が表示されれば OK）
for el in v4l2src jpegdec videoconvert webrtcsink vp8enc opusenc pulsesrc pipewiresrc; do
  printf "%-14s " "$el"; gst-inspect-1.0 $el >/dev/null 2>&1 && echo OK || echo MISSING
done

# カメラが api.v4l2.path 付きで検出されること（手順 6 が成功していれば 1 行出る）
gst-device-monitor-1.0 Video/Source 2>/dev/null | grep -E "api\.v4l2\.path"
```

ノートブックでのカメラ検出確認:

```python
import os
print("GI_TYPELIB_PATH =", os.environ.get("GI_TYPELIB_PATH"))
# device_detection は Gst.init を呼ばないので、ここで初期化する（デーモンは自動で行う）
import gi; gi.require_version("Gst", "1.0")
from gi.repository import Gst; Gst.init(None)
from reachy_mini.media.device_detection import get_video_device
print("Camera:", get_video_device()[0] or "(not found)")
```

---

## 9. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `undefined symbol: gst_h264_picture_get_user_data` などの警告 / `*** stack smashing detected ***` でクラッシュ | 1.20 プラグインが 1.24 コアに読み込まれている | 手順 5 で 1.20 ディレクトリを退避し、`GST_PLUGIN_PATH` から `/usr/lib/.../gstreamer-1.0` を外す |
| デーモンが `No camera found` / `mini.media.get_frame()` が `None`（音声は動く） | `pipewiredeviceprovider` が無く `api.v4l2.path` が得られない（手順 5 で 1.20 の `libgstpipewire.so` を無効化したため） | **手順 6** で PipeWire GStreamer プラグインを 1.24 向けにビルド |
| ノートブックで **カメラが見つからない** | `GI_TYPELIB_PATH` 未設定（1.20 の typelib を使用）。または手順 6 未実施 | 手順 7 で `.env` に設定 → **カーネル再起動**。それでも駄目なら手順 6 を確認 |
| `gst-inspect-1.0 jpegdec` が **MISSING** / カメラ録画が `Internal data stream error` | `libjpeg-dev` 無しでビルドした | `sudo apt install -y libjpeg-dev` 後に**再ビルド**（下記） |
| `x264enc` が MISSING | `-Dgpl=disabled`（既定） | 必要なら `libx264-dev` を入れ `-Dgpl=enabled` で再ビルド。WebRTC は VP8 で動作するため必須ではない |
| カメラ録画で `could not open camera`（デバイス使用中） | `reachy-mini-daemon` がカメラを占有 | デーモンを停止してから実行 |

### 依存追加後の再ビルド（例: `libjpeg-dev` を入れた後）

```bash
sudo apt install -y libjpeg-dev
cd ~/gstreamer-src
meson setup --reconfigure builddir
ninja -C builddir
sudo ninja -C builddir install && sudo ldconfig
rm -rf ~/.cache/gstreamer-1.0/
gst-inspect-1.0 jpegdec      # OK になるはず
```

---

## 参考

- Reachy Mini 公式手順: `reachy_mini/docs/source/SDK/gstreamer-installation.md`
- GStreamer: <https://gstreamer.freedesktop.org/>
- gst-plugins-rs: <https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs>
