# 自动邮件功能修改记录

## 已修改

- `labcheckin/mail.py`：新 SMTP 邮件发送模块，支持 SSL/STARTTLS、中文附件、邮件内容、邮箱/附件校验、发送错误分类。
- `labcheckin/agent.py`：从 `need-send` 事件创建本地持久化任务，检查文件稳定性，安全发送后回报台账；服务器回报失败时不会重复发送；文件夹生成可跨重启复用。
- `labcheckin/db.py`：增加 `booking_folders` 和 `booking_deliveries` 表，不删除或覆盖既有 `sessions/jobs` 表，迁移兼容。
- `labcheckin/config.py` 与 `config.json`：增加邮件安全模式、等待时间、自动安全重试参数，SMTP 凭据可通过环境变量传入。
- `labcheckin/mail_cli.py`：提供 `check`、`test --to`、`status`、`retry` 管理命令。
- `scripts/configure-mail.ps1`：Windows 配置向导，将邮箱授权码用当前 Windows 用户 DPAPI 加密保存。
- `scripts/start-device.ps1`：启动时自动读取加密授权码（优先使用环境变量）。
- `labcheckin/booking.py`：业务回报显式失败时不标记已回报。
- `tests/test_mail_feature.py`：自动化回归测试（模拟 SMTP 与预约服务，无真实邮件）。
- `EMAIL_FEATURE.md`、`DEPLOYMENT.md`：配置和部署说明。

## 没有修改

- 未改变手机在中央预约系统扫码上/下机的流程。
- 未改变原有预约接口路径与业务状态值。
- 未删除原有 SQLite 数据表和用户数据。
- 未要求在仪器电脑上额外安装 Python 邮件第三方库（使用标准库 `smtplib`）。

## 重要限制

上传包没有 `labpilot` 中央预约系统源码；仅能根据现有 desk 接口契约实现设备 Agent 端。未使用真实邮箱授权码或真实预约服务完成端到端测试。当前版本不会识别各实验软件的原始输出目录，需将实验文件保存到 Agent 为本次预约创建的目录；如果设备软件不支持自定义保存路径，需另做目录同步模块。


## 跨设备统一邮箱配置

- `start.cmd` 增加实验室默认 SMTP 主机、端口、安全连接和发件账号。
- 新增 `mail.env` / `mail.env.example`，由启动链路加载，授权码不再依赖单台 Windows 用户的 DPAPI。
- 新增 `scripts/load-mail-env.ps1`，仅允许加载 SMTP 白名单环境变量。
- `scripts/configure-mail.ps1` 改为一次性生成可迁移的 `mail.env`，并支持将旧版 DPAPI 授权码迁移到新配置。
- `scripts/test-mail.ps1` 支持读取可迁移配置；旧版 DPAPI 文件保留兼容读取。
- 新增 SMTP 环境变量覆盖配置的自动化测试。
- 安全说明：跨机器复用需要可迁移凭据，`mail.env` 中授权码为明文；已加入 `.gitignore`，只应在可信设备之间分发。
