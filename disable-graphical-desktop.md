# GNOMEデスクトップを無効化してメモリを節約する方法

Jetson Orin Nanoはメモリ(RAM)をCPUとGPUで共有しているため、GNOMEデスクトップ環境（gnome-shell、Xorg/Wayland、通知サービスなど）が常駐しているだけで数百MB〜1GB以上のメモリを消費します。ターミナル作業しか行わない場合、グラフィカルデスクトップを起動しないテキストモード（CUI）で運用することで、その分のメモリをWhisperやLLMなどのGPUワークロードに回すことができます。

## 現状の確認

以下のプロセスがGNOMEデスクトップ関連で常駐しています。

```bash
ps aux | grep -iE "gnome|gdm" | grep -v grep
```

- `gnome-shell` : デスクトップ本体（ウィンドウマネージャ、コンパジタ）
- `gnome-terminal-server` : ターミナルアプリのバックグラウンドプロセス
- `evolution-alarm-notify` / `gnome-shell-calendar-server` : 通知・カレンダー関連の常駐サービス

## テキストモード（CUI）へ変更する手順

### 1. デフォルトの起動ターゲットをテキストモードに変更

```bash
sudo systemctl set-default multi-user.target
```

### 2. 再起動

```bash
sudo reboot
```

再起動後はグラフィカルログイン画面（GDM）が起動せず、コンソールログインプロンプトが表示されます。SSH接続でのログインもそのまま利用できます。

### 3. 動作確認

再起動後、以下でGNOME関連プロセスが起動していないことを確認します。

```bash
ps aux | grep -iE "gnome|gdm" | grep -v grep
free -h
```

`free -h` の使用量（used）が、GUI起動時と比べて減っていれば成功です。

## グラフィカルデスクトップに戻したい場合

### 恒久的に戻す（次回再起動時からGUI起動）

```bash
sudo systemctl set-default graphical.target
sudo reboot
```

### 一時的にGUIを起動する（再起動せずに今だけ）

```bash
sudo systemctl start gdm
```

再度テキストモードに戻すには、GUIからログアウトするか以下を実行します。

```bash
sudo systemctl stop gdm
```

## 補足

- SSH経由でのターミナル作業には影響ありません。ネットワーク（ssh、Wi-Fi/Ethernet）はテキストモードでも通常通り動作します。
- リモートで画面が必要な場合は、VNCなど別途リモートデスクトップ環境を用意する必要があります（今回の用途では不要）。
- 本手順はWhisper（STT）モデル読み込み時に発生した `NVML_SUCCESS == r INTERNAL ASSERT FAILED` エラー（実質的にはGPU/共有メモリ不足によるCUDAメモリ確保失敗）の対策の一つです。

## なぜ `nvidia-smi` はJetsonでGPU使用率・メモリを表示しないのか

Jetsonは統合型（Tegra）GPUを搭載しており、CPUとGPUが物理的に同じRAMを共有するユニファイドメモリ構成になっています。ディスクリートGPU（PCIe接続のGPUカード）のようにNVIDIAがフル機能のNVMLバックエンドをTegra向けに提供していないため、`nvidia-smi` はGPUの存在は認識するものの、メモリ使用量などのクエリには `Not Supported` を返す簡易的な表示しかできません。

```bash
nvidia-smi
# Memory-Usage: Not Supported と表示される
```

### 代わりにJetsonでメモリ・GPU使用率を確認する方法

- **`tegrastats`**（標準搭載）: CPU+GPU共有RAM使用量、GPU周波数（`GR3D_FREQ`、GPU使用率に相当）、CPU負荷、温度などをリアルタイム表示。
  ```bash
  tegrastats
  ```

- **`jtop`**（`jetson-stats` パッケージ、要インストール）: `htop` のようなTUIで、GPU使用率・RAM・プロセスごとの内訳・スワップ・電力などを見やすく表示。
  ```bash
  sudo pip3 install -U jetson-stats
  sudo reboot   # jtopのサービスを起動するために再起動が必要
  jtop
  ```

- **`free -h`**: JetsonではGPUメモリ＝システムRAMなので、全体のRAM使用状況からGPUのメモリ逼迫状況も間接的に把握できます。
  ```bash
  free -h
  ```
