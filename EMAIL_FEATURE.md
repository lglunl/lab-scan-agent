# 自动邮件功能（仪器电脑 Agent）

## 1. 当前项目的真实架构

本代码仓库**仅包含 Python 仪器电脑端 Agent**，不包含中央预约服务 `labpilot` 的源码。已接入项目原有协议：

1. 用户在预约系统完成上机扫码；中央服务 `GET /api/instruments/{instrument_id}/desk` 返回 `phase=checked-in`。
2. Agent 自动创建与预约编号绑定、重启后仍保持相同路径的独立实验文件夹，通过 `POST /api/instruments/{instrument_id}/desk` 回报 `folderPath`。
3. 用户在该文件夹内保存/复制实验软件产生的文件。
4. 用户在预约系统扫码下机并填写邮箱；中央服务返回 `phase=need-send`，同时返回 `booking.id`、`email`、`folderPath`、`applicant`、`checkInAt`、`checkOutAt`。
5. Agent 将邮件任务登记到 `data/lab.sqlite`，连续两次检查文件大小/修改时间未发生变化，且满足稳定时间，再通过 SMTP 自动发送附件。
6. Agent 将 `sendStatus=sent / failed / skipped` 与 `sendError` 回报中央服务。若回报阶段掉线，会仅重试**回报**，不会再发送一次邮件。

**注意：现有项目并没有通用的“监控实验仪器原始输出目录并自动归属文件”的功能。** 此版本只处理与预约绑定的独立实验文件夹。仪器软件若默认保存到其他目录，应将其导出目录调整到本次实验文件夹，或单独开发设备专用同步模块。不要把多个用户共享的输出目录直接作为邮件来源。

## 2. 发送前需要准备

- 准备可以通过 SMTP 发信的实验室邮箱，获得 SMTP 主机、端口及**应用授权码**（部分邮件服务商需单独启用 SMTP）。
- 在 `config.json` 保留原有 `booking_api_base`、`instrument_id`、`require_booking` 配置。
- 将发送参数通过**环境变量**设置，尤其不要把邮箱授权码写入 `config.json` 或提交到 Git。
- 确认仪器电脑能够访问预约服务器与 SMTP 邮件服务器。

### Windows PowerShell 临时设置示例（不存储到代码）

```powershell
$env:LAB_SMTP_HOST = "smtp.example.com"       # 改成真实 SMTP 服务器
$env:LAB_SMTP_PORT = "587"
$env:LAB_SMTP_SECURITY = "starttls"
$env:LAB_SMTP_USER = "lab@example.com"
$env:LAB_SMTP_FROM = "lab@example.com"
$env:LAB_SMTP_PASSWORD = "邮箱专用授权码"

# 检查配置。仅显示是否已设置密码，不会输出密码。
python -m labcheckin.mail_cli check

# 发送一封带测试附件的邮件到你自己的测试邮箱（会真实发信）
python -m labcheckin.mail_cli test --to "your-test@example.com"

# 同一 PowerShell 窗口里启动设备 Agent，继承以上环境变量
python -m labcheckin
```

也可以在同一个 PowerShell 窗口执行 `./start.cmd`，它会继承以上环境变量。`start.cmd` 会在首次启动时下载 Python，因此必须在联网且符合实验室 IT 安全策略的电脑上使用。建议长期部署改为受管理的 Windows 服务账号/凭据管理方案。

对 465 端口通常使用：

```powershell
$env:LAB_SMTP_PORT = "465"
$env:LAB_SMTP_SECURITY = "ssl"
```

`none` 模式仅供受信任的**不需要登录**的内网 SMTP 中继使用；不允许在未加密的连接上传送 SMTP 登录凭证。

### config.json 中新增选项

| 字段 | 默认值 | 说明 |
|---|---|---|
| `smtp_security` | `starttls` | `starttls`、`ssl`、`none` |
| `smtp_timeout` | `30` | SMTP 连接超时（秒） |
| `file_stable_seconds` | `10` | 文件两次检查均稳定且最后修改超过此时长，才发送 |
| `file_wait_seconds` | `120` | 下机后无数据文件，最长等待此时长；超时标记失败 |
| `email_max_attempts` | `3` | 在**确定尚未发送**的网络错误上安全重试的最大次数 |
| `max_attachment_bytes` | `20971520` | 附件文件原始大小限制（默认 20 MiB） |

**提醒：**附件经过 MIME/Base64 编码后通常大于文件原始大小，20 MiB 原始数据可能超出邮箱提供商限制。实验数据更大时，应扩展为内网服务器/NAS/对象存储的加密下载链接；本版不自动上传大文件到云盘。

## 3. 邮件任务状态和故障保护

| 状态 | 含义 | 后续操作 |
|---|---|---|
| `queued` | 已检测到下机，等待文件稳定/重试时间 | 自动继续 |
| `sending` | 已开始尝试交付 SMTP | 不允许重复任务 |
| `sent` | SMTP 服务器已接收邮件 | 回报预约服务器，不再重复发送 |
| `failed` | 明确未发送；例如邮箱配置错误、附件过大、文件缺失 | 排查后人工重试 |
| `skipped` | 用户未填写邮箱 | 回报跳过 |
| `uncertain` | 发送期间断电/断线，可能已投递 | **先查收件箱/邮件服务器日志，再决定是否允许重发** |

查询某个预约邮件状态：

```powershell
python -m labcheckin.mail_cli status "预约ID"
```

修复配置或补充实验文件后，人工重试明确失败的任务：

```powershell
python -m labcheckin.mail_cli retry "预约ID"
```

只有确认重复邮件可以接受后，才能重试“是否已发送不确定”的任务：

```powershell
python -m labcheckin.mail_cli retry "预约ID" --confirm-uncertain
```

**关于自动重试：**连接 SMTP 失败且可以确认还没开始发送邮件时会自动退避重试（15s、30s 等，最多按配置次数）；如果在数据提交阶段断线，不能确认邮件服务器是否已收到，则**不会**自动再发，避免重复投递。

## 4. 已完成的自动化测试

运行：

```bash
python -m pytest -q tests/test_mail_feature.py
```

测试覆盖模拟 SMTP、附件与中文文件名、恶意邮箱地址、附件上限、软链接越界、文件夹越权、文件稳定性、模拟预约服务器回报中断、重启后的状态恢复等。

这些测试**不需要真实邮箱、不会对外发送邮件**。实际 SMTP 测试需自行提供授权码和测试邮箱；中央服务端 `labpilot` 源码未包含在本压缩包中，因此没有对真实中央服务做端到端联调。

## 5. 已知限制与上线建议

1. 只发送对应预约目录内的数据，不会自动识别仪器软件的专有数据库，也不能保障实验软件没有在晚于设定稳定期的时间才输出新文件。需按设备实测调整等待与保存规则。
2. 邮件 `sent` 表示**SMTP 服务器接受了邮件**，不代表收件人最终阅读或一定送达收件箱；仍可能退信或进入垃圾箱。
3. 邮件地址由预约服务器下发。本仓库无法验证该邮箱是否属于登录用户；生产环境应在**预约服务器**使用已验证的用户邮箱，并限制邮箱修改，避免研究数据外泄。
4. 原项目 Windows 启动脚本会把预约 API Token 写到 `config.json`（尽管原部署文档建议使用环境变量）。这是多电脑部署时需要单独修复的凭据管理风险。建议用 Windows Credential Manager/DPAPI 或受管理的服务账号安全存储。
5. 当前台账报错通过原有 `report_desk` 接口回传，中央系统界面中具体展示效果取决于 `labpilot` 的实现；如要增加管理员邮件失败看板，须修改预约服务器源码。
6. 每台仪器运行**一个** Agent 进程，不要同时启动两个实例。为严谨上线，还应提供系统级单实例锁、监控告警、日志轮转和真实邮箱/多仪器联调。

## 6. Windows 电脑长期使用（免每次输入 SMTP 授权码）

对直接双击 `start.cmd` 的 Windows 电脑，推荐先在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure-mail.ps1
```

按提示输入 SMTP 主机、端口、发信邮箱及授权码。脚本把非敏感参数写入 `config.json`，把授权码通过**当前 Windows 用户的 DPAPI**加密保存到 `data/.smtp-password.dpapi`。以后双击 `start.cmd`，会自动解密到当前进程的环境变量里。

注意：加密文件只适合由**相同 Windows 用户在同一电脑**上使用，不要将加密文件复制到其他仪器电脑。此 PowerShell 脚本未在当前 Linux 验证环境实际运行，第一次部署请在测试仪器电脑验证。项目原有的预约系统 Token 保护问题仍然需要另外处理。
