# 实验室扫码上机 Agent（lab-scan-agent）

运行在仪器电脑上的扫码上机 / 下机 Agent：仪器电脑轮询预约服务器，扫码上机后自动创建数据文件夹、把实验软件生成的数据复制到文件夹，下机时把数据通过邮件发送给预约人。

## 快速开始

1. 把整个项目文件夹复制到仪器电脑。
2. 双击 `start.cmd`，首次运行会提示填写设备 ID、密钥等，并自动准备本地 Python 运行环境。
3. 保持该窗口开启，Agent 每 5 秒轮询一次预约状态并同步数据。

## 配置

- 设备与服务器配置在项目根目录的 `config.json`。
- 统一邮箱（SMTP）配置在 `mail.env`（由 `scripts\configure-mail.ps1` 生成），模板见 `mail.env.example`。
- 更多说明见 `DEPLOYMENT.md`、`EMAIL_FEATURE.md`、`邮箱配置.md`。

## 注意事项

### 1. 注意同步服务器公网链接

预约服务器（labpilot）通过 Cloudflare Quick Tunnel 暴露公网地址，**每次 tunnel 重启后公网 URL 都会变化**（形如 `https://xxxx.trycloudflare.com`）。

URL 变化后必须同步两处，否则设备将无法上报 / 查询预约：

- 服务器 `.env` 中的 `PUBLIC_BASE_URL`
- 本机 `config.json` 中的 `booking_api_base`

两边保持一致，设备端扫码上机 / 下机才能正常访问服务器。

### 2. 已去掉测试时自动生成文件的功能

早期版本在扫码上机后会自动生成 `DEMO_` 开头的模拟数据文件（用于测试「预约 → 建文件夹 → 扫码下机 → 邮件附件」流程）。这些不是真实实验数据，会污染真实数据文件夹。

该功能现已**完全移除**：

- 删除 `demo_seed_on_checkin` 配置项与 `labcheckin/demo_data.py`（自动生成逻辑）。
- 上机、下机、发邮件流程中不再有任何自动生成测试文件的代码，实验文件夹里只会出现真实实验数据。
