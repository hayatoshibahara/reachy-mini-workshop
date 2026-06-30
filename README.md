# Reachy Mini ワークショップ

## 概要

1. [Reachy Mini SDK](1_reachy_mini_sdk.ipynb)


```sh
GST_DEBUG="2,v4l2src:6,jpegdec:5,GST_CAPS:5" \
  reachy-mini-daemon --log-level DEBUG --log-file /tmp/reachy_daemon.log
```

```sh
reachy-mini-daemon --no-wake-up-on-start
```