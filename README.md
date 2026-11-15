# Reachy Mini ワークショップ

## 概要

Reachy Mini SDK と OpenAI API を使い、シンプルなカスケード型パイプラインをシミュレータ上で構築するワークショップの資料です。

## 資料

- [GitHub](https://github.com/hayatoshibahara/reachy-mini-workshop/blob/main/main.ipynb)
- [Google Colab](https://colab.research.google.com/github/hayatoshibahara/reachy-mini-workshop/blob/main/main.ipynb)（読むためのリンクです。シミュレータは Colab では動きません。実行は下の Codespaces かローカル環境で行ってください）

## 使い方（GitHub Codespaces・おすすめ）

ブラウザだけで始められます。シミュレータ、GStreamer、Python の環境は用意済みです。

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/hayatoshibahara/reachy-mini-workshop)

1. 上のボタンから Codespace を作成する（GitHub アカウントが必要）
2. 起動すると、シミュレータのビューア（ポート 8080）が別のタブで開く
   - 開かない場合は、VS Code の「ポート」タブで 8080 の地球儀アイコンを押す
3. ビューア左下のボタンで、自分の PC のデバイスをロボットにつなぐ
   - 🎤 マイク: ロボットのマイクとして使う
   - 🔊 スピーカー: ロボットが話す声をこのタブで鳴らす
   - 📷 カメラ: ロボットのカメラとして使う
   - 初回はブラウザがマイク・カメラの使用許可を求めるので「許可」を選ぶ
   - スピーカーとマイクを同時に使うときは、**ヘッドホンを使う**（ロボットが自分の声を拾わないように）
4. VS Code で `main.ipynb` を開くか、`script/` のスクリプトをターミナルで実行する

OpenAI API キーは、Codespaces のシークレット `OPENAI_API_KEY` に登録します（GitHub の Settings → Codespaces → Secrets）。

使い終わったら、[Codespaces の一覧](https://github.com/codespaces) から停止・削除してください（無料枠を超えると料金がかかります）。

### シミュレータの再起動・ログ

```sh
bash .devcontainer/start.sh   # 止まっているものだけ起動し直す
tail -f /tmp/daemon.log       # デーモンのログ（ほかに /tmp/viewer.log, /tmp/pulseaudio.log）
```

カメラは既定でブラウザのカメラを使います。シミュレータ内のカメラ（ロボットの頭から見た 3D の景色、約 1fps）を使う場合は、デーモンとビューアを止めてから起動し直します。

```sh
pkill -f bin/reachy-mini-daemon; pkill -f viewer/server.py; sleep 5
REACHY_MINI_CAMERA=sim bash .devcontainer/start.sh
```

## 使い方（ローカル環境）

1. リポジトリをローカル環境にダウンロード
  ```sh
  git clone https://github.com/hayatoshibahara/reachy-mini-workshop.git
  ```
2. [Visual Studio Code](https://code.visualstudio.com/) をインストールし、ダウンロードしたリポジトリを開く
3. `main.ipynb` を開き、開発をはじめる
