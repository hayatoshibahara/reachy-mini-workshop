"""音声・カメラデバイスの確認と、音量の設定。"""

from reachy_mini.daemon.app.routers.volume_control import get_volume_control
from reachy_mini.media.camera_constants import (
    ArducamSpecs,
    MujocoCameraSpecs,
    ReachyMiniLiteCamSpecs,
    ReachyMiniWirelessCamSpecs,
)
from reachy_mini.media.device_detection import (
    get_audio_device,
    get_video_device,
    gst_monitor_devices,
)

for title, device_class in [
    ("入力音声デバイス（audio source）", "Audio/Source"),
    ("出力音声デバイス（audio sink）", "Audio/Sink"),
    ("カメラデバイス（video source）", "Video/Source"),
]:
    print(f"\n=== {title} ===")
    for d in gst_monitor_devices(device_class):
        print(f"[{d.index}] {d.display_name}")
        print(f"class={d.device_class}  props={dict(list(d.properties.items())[:4])}")

print("\n=== ロボットが使用しているデバイス ===")
mic_id = get_audio_device("Source")
spk_id = get_audio_device("Sink")
cam_path, cam_specs = get_video_device()

print(f"Microphone : {mic_id or '(not found / sim モードでは未使用)'}")
print(f"Speaker    : {spk_id or '(not found / sim モードでは未使用)'}")
print(f"Camera     : {cam_path or '(not found — sim モードは MuJoCo UDP 経由)'}")

print("\n=== 現在のオーディオ音量 ===")
vc = get_volume_control()
print(f"プラットフォーム : {vc.platform_name}")
print(f"スピーカー  ({vc.output_device.name}): {vc.get_output_volume()}%")
print(f"マイク      ({vc.input_device.name}) : {vc.get_input_volume()}%")

print("\n=== スピーカーとマイクの音量を100%に設定 ===")
ok_out = vc.set_output_volume(100)
ok_in = vc.set_input_volume(100)
print(f"スピーカー  : {'✓ 100%' if ok_out else '✗ 設定失敗'}")
print(f"マイク      : {'✓ 100%' if ok_in else '✗ 設定失敗'}")
print(f"設定後 — スピーカー: {vc.get_output_volume()}%  マイク: {vc.get_input_volume()}%")

print("\n=== カメラの仕様 ===")
if cam_specs:
    print(f"カメラの仕様: {cam_specs}")
else:
    print("カメラの仕様は取得できませんでした。")

print("\n=== カメラの解像度とフレームレートの一覧 ===")
for specs_cls in [MujocoCameraSpecs, ReachyMiniLiteCamSpecs, ReachyMiniWirelessCamSpecs, ArducamSpecs]:
    specs = specs_cls()
    print(f"\n【{specs.name}】 デフォルト: {specs.default_resolution.name}")
    for r in specs.available_resolutions:
        w, h, fps, _ = r.value
        marker = " ← default" if r == specs.default_resolution else ""
        print(f"  {w:4d}x{h:<4d} @{fps:2d}fps  ({r.name}){marker}")
