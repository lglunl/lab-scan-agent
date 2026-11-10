# 扫码 agent 部署文档（设备电脑端）

这份文档讲的是**把扫码 agent 装到仪器电脑上**该怎么配。
预约服务器（labpilot）那边的部署见它的 `docs/DEPLOYMENT.md`。

## 架构

```
        ┌──────────────────────┐
        │  预约服务器 labpilot    │  ← 跑在一台固定机器上，有公网网址
        │  预约 / 台账 / 看板      │     (当前是 xxx.trycloudflare.com)
        └──────────▲───────────┘
                   │ HTTPS（带 Bearer 密钥）
     ┌─────────────┼──────────────────┐
     ▼             ▼                  ▼
┌─────────┐  ┌─────────┐        ┌─────────┐
│ 设备电脑1 │  │ 设备电脑2 │  ...   │ 设备电脑N │
│ 扫码 agent │  │ 扫码 agent │        │ 扫码 agent │
│ 流式细胞仪 │  │ 共聚焦   │        │ PCR      │
└─────────┘  └─────────┘        └─────────┘
```

- **每台仪器电脑各放一份**扫码 agent 代码，各自独立运行。
- 设备电脑只需要能上网即可，不需要公网 IP。
- 设备电脑通过 `instrument_id` 告诉预约服务器「我管的是哪台仪器」。

---

## 部署前，先从预约服务器拿 3 样东西

| 要拿的东西 | 说明 | 在哪看 |
|---|---|---|
| ① 服务器公网网址 | `https://xxx.trycloudflare.com` | 预约服务器上跑 cloudflared 时输出的网址 |
| ② 密钥 `LAB_API_TOKEN` | 所有设备共用的同一个密钥 | 预约服务器的 `.env` 里的 `LAB_API_TOKEN` |
| ③ 仪器 ID `instrument_id` | 这台电脑对应哪台仪器 | 见下 |

**仪器 ID 在哪查**（两种方式）：

- 看预约服务器代码 `server/db.js` 里的 `SEED_INSTRUMENTS`；
- 或调接口（带密钥）：

```powershell
curl.exe -H "Authorization: Bearer 你的密钥" https://xxx.trycloudflare.com/api/instruments
```

返回的每个对象的 `id` 就是仪器 ID，例如：
`inst_fc`（流式细胞）、`inst_clsm`（共聚焦）、`inst_pcr`（PCR）、`inst_centrifuge`（离心机）。

---

## 在设备电脑上填写配置

三个关键项都在这份代码的 **`config.json`** 和**环境变量**里，如下：

### 1. `config.json`（填服务器网址 + 仪器 ID）

```json
{
  "device_id": "local-demo",
  "device_name": "流式细胞仪",
  "host": "127.0.0.1",
  "port": 8787,
  "booking_api_base": "https://xxx.trycloudflare.com",
  "booking_api_token": "",
  "instrument_id": "inst_fc",
  "require_booking": true
}
```

字段说明（只列部署相关的，其余保持默认即可）：

| 字段 | 填什么 | 必填 |
|---|---|---|
| `device_id` | 本机唯一标识（本地用，随便取，别和别的机器重复） | 是 |
| `device_name` | 界面上显示的设备名 | 否 |
| `booking_api_base` | ① 服务器公网网址，末尾不要带 `/` | 是 |
| `booking_api_token` | **留空**。密钥走环境变量，不要写在这里 | — |
| `instrument_id` | ③ 这台电脑对应的仪器 ID | 是 |
| `require_booking` | 是否强制「有预约才能上机」，一般 `true` | 是 |

### 2. 密钥（用环境变量 `LAB_API_TOKEN`，不写进 config.json）

密钥若写进 `config.json` 会被提交到 git 泄露，所以**用环境变量提供**。
扫码 agent 启动时会优先读环境变量 `LAB_API_TOKEN`。

- 临时（当前 PowerShell 窗口生效）：

```powershell
$env:LAB_API_TOKEN = "你的密钥"
python -m labcheckin
```

- 永久（系统环境变量，重启也生效）：

```powershell
setx LAB_API_TOKEN "你的密钥"
```

> 注意：`setx` 改的是新进程生效，需要重开一个 PowerShell 再启动。

---

## 启动

```powershell
cd 扫码agent目录
pip install -r requirements.txt        # 首次部署
$env:LAB_API_TOKEN = "你的密钥"
python -m labcheckin
```

启动后：

- 本机浏览器打开 `http://127.0.0.1:8787` 能进登录页；
- 程序会自动开一条 quick tunnel，把本机服务暴露成公网二维码地址，写到 `data/public_base_url.txt`（用户手机扫码访问用的）。

---

## 验证

1. 用预约系统里已注册的用户名 + 密码登录，应能通过。
2. 该用户在当前时段对 `instrument_id` 有预约时，点「上机」成功；无预约会提示「没有有效预约」。
3. 上机后，预约服务器的台账/看板里能看到这条预约变成「已上机」；下机后变「已闭环」。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 登录提示「预约服务暂时不可用」 | `booking_api_base` 填错，或服务器没开 / 隧道失效 | 检查网址、确认服务器 cloudflared 在跑 |
| 接口返回 401 | 密钥不对或没设 | 确认 `$env:LAB_API_TOKEN` 与服务器 `.env` 的 `LAB_API_TOKEN` 一致 |
| 上机提示「没有有效预约」 | 用户没预约，或 `instrument_id` 填错 | 核对 `instrument_id` 是否等于服务器的仪器 ID |
| 二维码点开提示「设备不匹配」 | 扫码 URL 里的 `device` 与 `device_id` 不一致 | 用本机 `data/public_base_url.txt` 里的二维码 |

---

## 密钥说明

- **所有设备电脑用同一个密钥**，它就是预约服务器 `.env` 里的 `LAB_API_TOKEN`。
- 这是「机器与机器之间」的接口凭证，不是用户密码；用户登录用的是各自账号密码。
