#!/usr/bin/env bash
# Codespace（devcontainer）の起動ごとに、シミュレータとビューアをバックグラウンドで立ち上げる。
# ログ: /tmp/pulseaudio.log, /tmp/daemon.log, /tmp/viewer.log
set -u
cd "$(dirname "$0")/.."

SCENE="${REACHY_MINI_SCENE:-minimal}"
# browser: ビューアから参加者の PC のカメラを使う（既定）。sim: シミュレータのカメラを使う
CAMERA="${REACHY_MINI_CAMERA:-browser}"

# 音声デバイスがないので、PulseAudio のダミー（null sink）を 2 つ作り、ビューア経由でブラウザにつなぐ
#   speaker     … 既定の出力。デーモンが再生した音を、そのモニターからブラウザに送る
#   browser_mic … ブラウザのマイク音声を流し込む先。そのモニターを既定の入力にする
if ! pactl info > /dev/null 2>&1; then
    pulseaudio --daemonize=yes --exit-idle-time=-1 --log-target=file:/tmp/pulseaudio.log
    for _ in $(seq 1 20); do pactl info > /dev/null 2>&1 && break; sleep 0.5; done
fi
pactl list short sinks | grep -q "speaker" \
    || pactl load-module module-null-sink sink_name=speaker > /dev/null
pactl list short sinks | grep -q "browser_mic" \
    || pactl load-module module-null-sink sink_name=browser_mic > /dev/null
pactl set-default-sink speaker
pactl set-default-source browser_mic.monitor

if ! pgrep -f "bin/reachy-mini-daemon" > /dev/null; then
    if [ "$CAMERA" = "sim" ]; then
        # シミュレータのカメラは --headless では起動しないため、Xvfb 上でビューア付きで動かす。
        # 3D 表示はブラウザのビューアで見るので、MuJoCo のウィンドウは小さくし、CPU 描画（llvmpipe）の
        # スレッドも 1 本に絞る（4 コア中 約 4.3 → 1.6 コア。物理演算は実時間のまま）
        LP_NUM_THREADS=1 nohup xvfb-run -a -s "-screen 0 320x240x24" \
            reachy-mini-daemon --sim --scene "$SCENE" > /tmp/daemon.log 2>&1 &
    else
        # カメラ映像はビューアが UDP 5005 に流すので、MuJoCo の描画は止める（Xvfb も不要）
        nohup reachy-mini-daemon --sim --scene "$SCENE" --headless > /tmp/daemon.log 2>&1 &
    fi
fi

# デーモンはバックエンドの準備を 2 秒しか待たない（SDK 内で固定）。CPU 描画の初回起動（sim）では
# 間に合わず state が error になることがあるので、API で再起動する（2 回目以降は間に合う）
daemon_state() {
    curl -s localhost:8000/api/daemon/status 2> /dev/null \
        | python -c "import json, sys; print(json.load(sys.stdin)['state'])" 2> /dev/null
}

# 起動・再起動の途中（starting など）を抜けるまで待ち、最終的な状態を返す
wait_daemon_settled() {
    local state=""
    for _ in $(seq 1 60); do
        state=$(daemon_state)
        case "$state" in
            running | error) break ;;
        esac
        sleep 1
    done
    echo "$state"
}

state=$(wait_daemon_settled)
for attempt in 1 2 3; do
    [ "$state" = "running" ] && break
    echo "デーモンの状態が ${state:-不明} のため再起動します（${attempt} 回目）"
    curl -s -X POST localhost:8000/api/daemon/restart > /dev/null
    sleep 2
    state=$(wait_daemon_settled)
done
echo "デーモンの状態: ${state:-不明}"

if ! pgrep -f "viewer/server.py" > /dev/null; then
    nohup python viewer/server.py --scene "$SCENE" --camera "$CAMERA" > /tmp/viewer.log 2>&1 &
fi

echo "シミュレータを起動しました（シーン: $SCENE、カメラ: $CAMERA）。ビューア: ポート 8080"
