# 微信强提醒助手

本轮详细开发记录见：`文档/微信强提醒助手本轮开发工作总结_2026-07-21.md`。

Windows 桌面端微信消息强提醒工具。当前正式主链路为：

```text
自动定位微信窗口 -> 消息区截图 -> PaddleOCR -> 去重与规则匹配 -> 本地强提醒 -> 超时通知
```

固定区域 OCR 作为兜底模式。历史微信弹窗 OCR 仅兼容保留，新配置默认关闭。

## 运行环境

- Windows
- Python 3.11
- Flet 0.24.1
- PaddleOCR 2.7.3

项目内已有专用虚拟环境时，直接运行：

```powershell
.\run_wechat_alert_py311.ps1
```

前台调试：

```powershell
$env:PYTHONUTF8='1'
.\.venv-wechat-alert-py311\Scripts\python.exe -m wechat_alert_assistant
```

重启当前项目实例：

```powershell
.\restart_wechat_alert_py311.ps1
```

只预览将要停止和启动的进程：

```powershell
.\restart_wechat_alert_py311.ps1 -WhatIf
```

安装真实监控依赖：

```powershell
.\install_real_monitor_deps_py311.ps1
```

## 当前能力

- 自动枚举并选择微信聊天窗口
- 预览、框选和保存窗口内消息区域
- 自动窗口 OCR 与固定区域 OCR
- 启动时忽略当前历史文本
- 按来源隔离的逐行模糊去重
- 自动窗口与固定区域跨来源提醒去重
- 群名、@ 名称、关键字规则匹配
- @ 名称 OCR 轻微误差容错
- 置顶强提醒、内置/导入报警音、循环播放和二次处理确认
- 电脑保活，防止自动熄屏、睡眠和空闲锁屏
- 模拟点击通知
- 通知并发保护、失败自动重试、日志轮转和调试截图清理

## 关键配置

- `dedup_seconds`：文本离开当前截图后仍保留的去重时间。
- `dedup_similarity`：OCR 行相似阈值，默认 `0.88`。
- `cross_source_dedup_seconds`：两种 OCR 模式识别到同一事件时的合并窗口，默认 `12` 秒。
- `fuzzy_name_threshold`：@ 名称模糊匹配阈值，默认 `0.88`。
- `keep_awake_enabled`：是否开启电脑保活，默认开启。
- `keep_awake_interval_seconds`：保活刷新间隔，默认 `240` 秒。
- `keep_awake_simulate_input`：是否发送无感 F15 按键刷新系统空闲时间，默认开启。
- `keep_awake_mouse_nudge`：是否用鼠标 1 像素往返作为兜底保活，默认开启。
- `alarm_sound`：报警音。可使用 `audio/` 中的预设，如 `preset:reflection`、`preset:surge`、`preset:dreamer`，也可保存导入音频文件路径。
- `retry_attempts`：通知失败后最多再试次数，默认 `2`。
- `retry_delay_seconds`：通知失败后等待多久再试，默认 `60` 秒。

正在截图中持续出现的行会刷新保留时间，不会仅因去重时间到期而重新报警。

## 测试

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv-wechat-alert-py311\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-wechat-alert-py311\Scripts\python.exe -m compileall -q .\wechat_alert_assistant .\tests
.\.venv-wechat-alert-py311\Scripts\python.exe -m pip check
```

## 配置安全

首次运行会创建本机 `config.json`。仓库提供的 `config.example.json` 不含联系人和坐标。

`automation` 通知会真实控制鼠标、粘贴文本并可能按 Enter 发送。测试前应使用隔离窗口，不能把自动通知测试当作无副作用操作。

`config.json`、日志和 OCR 截图可能包含隐私数据，已加入 `.gitignore`，不应提交到版本库。

## 当前限制

- OCR 准确率仍受微信字体、缩放比例和消息区裁剪范围影响。
- 自动窗口裁剪覆盖窗口超过 85% 时，测试结果会提示重新框选消息列表。
- 模拟点击通知依赖固定屏幕坐标。
- 如果模拟点击依赖或坐标持续错误，通知会在达到重试上限后停止自动重试。
- 托盘、打包、多屏和高 DPI 仍需继续验证。
