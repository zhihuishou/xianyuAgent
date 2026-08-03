# 闲鱼 AI 客服 — 完整链路说明

## 一、整体架构

```
闲鱼买家发私信
      │
      ▼
DingTalk IMPaaS WebSocket
wss://wss-goofish.dingtalk.com/
      │
      ▼
goofish_live.py  ←──── cookies.json（持久化登录态）
  │  handle_message()
  │    ├── 系统包（已读/在线）→ 跳过
  │    ├── 店主自己发消息   → 标记会话为人工介入，后续静默
  │    ├── 已人工介入会话   → 跳过 AI
  │    └── 买家消息        → ask() → AI 回复
  │
  ├── ai_agent.py  →  luxee.ai / OpenAI 兼容接口
  │     └── system prompt = soul.md + skills.md
  │
  ├── utils/feishu_notify.py  →  飞书 App API
  │     └── Cookie 失效时推送二维码图片到飞书
  │
  └── utils/cookie_store.py   →  cookies.json 读写
```

---

## 二、文件清单

| 文件 | 作用 |
|---|---|
| `goofish_live.py` | 主进程：WS 连接、消息路由、AI 回复、心跳、token 刷新 |
| `goofish_apis.py` | 闲鱼 HTTP API 封装：扫码登录、获取/刷新 token、签名 |
| `ai_agent.py` | AI 调用封装：加载 soul/skills，调用 LLM，返回回复文本 |
| `soul.md` | AI 人设定义：名字、语气、行为准则 |
| `skills.md` | AI 能力边界：允许回答的话题 + 禁止事项 + 兜底话术 |
| `utils/cookie_store.py` | Cookie 序列化：save / load / clear |
| `utils/feishu_notify.py` | 飞书通知：文字消息 + 二维码图片推送 |
| `utils/goofish_utils.py` | 工具函数：签名、加解密、cookie 转换 |
| `message.py` | 消息类型定义：`make_text()` / `make_image()` |
| `log_viewer.py` | 本地日志查看器，`http://localhost:8765` |
| `.env` | 所有密钥和配置（不提交 git） |
| `cookies.json` | 持久化登录态（不提交 git） |
| `logs/` | 按天滚动的运行日志，保留 7 天 |
| `requirements.txt` | Python 依赖 |

---

## 三、配置说明（.env）

```dotenv
# AI 接口（OpenAI 兼容格式）
DEEPSEEK_API_KEY=sk-xxxxxx
DEEPSEEK_BASE_URL=https://api.luxee.ai/v1
DEEPSEEK_MODEL=gpt-5.5

# 飞书 App（用于 Cookie 失效时推送二维码）
FEISHU_APP_ID=cli_xxxxxx
FEISHU_APP_SECRET=xxxxxx
FEISHU_RECEIVE_ID=xxxxxx          # 飞书用户 user_id

# Cookie 文件路径（相对于 goofish_live.py 运行目录）
COOKIES_FILE=cookies.json
```

**获取飞书 RECEIVE_ID**：飞书开放平台 → 用户信息 → user_id（数字格式）。

---

## 四、启动流程

```
python goofish_live.py
```

```
启动
  ├── 读取 .env
  ├── 检查 cookies.json
  │     有  → 直接加载登录态，跳过扫码
  │     没有 → 终端显示二维码（同时推送到飞书）→ 扫码 → 保存 cookies.json
  │
  ├── 建立 WebSocket 连接
  │     wss://wss-goofish.dingtalk.com/
  │
  ├── 注册 /reg（携带 accessToken）
  ├── 发送 ackDiff 同步状态
  │
  ├── 后台线程：每 10 分钟 refresh_token
  │     失败 → 飞书推送通知 → 删除 cookies.json → 退出
  │
  ├── 后台协程：每 15 秒发心跳 /!
  │
  └── 主循环：接收消息 → handle_message()
```

---

## 五、消息处理逻辑（handle_message）

```
收到 WS 消息
  │
  ├── 解包：body.syncPushPackage.data[0].data
  ├── 解密：decrypt()  →  Protobuf JSON
  │
  ├── msg_obj["1"] 是 list？  →  系统包（已读回执/在线状态），return
  │
  ├── 读取字段：
  │     node10.senderUserId   = 发送者 ID
  │     node10.reminderTitle  = 发送者昵称
  │     node10.reminderContent = 消息文本
  │     node10.reminderUrl    = 包含 itemId（当前商品 ID）
  │     node1["2"]            = cid（会话 ID）
  │
  ├── senderUserId == self.myid（店主）？
  │     是 → 将 cid 加入 _human_cids，后续该会话 AI 静默，return
  │
  ├── cid 在 _human_cids 中？
  │     是 → 跳过 AI，return
  │
  └── 调用 ask(消息文本, item_info=itemId)
        → AI 生成回复
        → send_msg() 发回给买家
```

---

## 六、AI 回复逻辑（ai_agent.py）

```python
system prompt = soul.md（人设）+ skills.md（能力边界）
user message  = 买家发送的文本
item_info     = 当前商品 ID（传入 system 上下文）

调用：POST https://api.luxee.ai/v1/chat/completions
模型：gpt-5.5（可在 .env 中更换）
超时：20 秒
最大输出：500 tokens
```

**定制 AI 行为**：
- 修改 `soul.md` 调整人设和语气，无需重启（进程启动时加载一次）
- 修改 `skills.md` 调整允许/禁止的话题

---

## 七、Cookie 持久化（utils/cookie_store.py）

| 函数 | 时机 |
|---|---|
| `save_cookies(session)` | 扫码登录成功后立即保存 |
| `load_cookies()` | 每次启动时优先尝试加载 |
| `clear_cookies()` | token 失效检测到后清除，触发下次重新扫码 |

只保存 `.goofish.com` 域下的 cookie，其他域过滤掉。

---

## 八、飞书通知（utils/feishu_notify.py）

| 函数 | 触发场景 |
|---|---|
| `notify_feishu(title, content)` | token 失效、refresh_token 报错 |
| `notify_feishu_qrcode(qr_url)` | 扫码登录时（同步推送二维码图片） |

流程：获取 `tenant_access_token` → 上传二维码 PNG → 发文字提示 + 发图片消息。
失败时降级为纯文字发送链接。

---

## 九、日志

运行日志写入 `logs/xianyu_YYYY-MM-DD.log`，按天滚动，保留 7 天。

**本地查看**：启动 `log_viewer.py` 后访问 `http://localhost:8765`，每 2 秒自动刷新。

```
python log_viewer.py
```

关键日志标识：

| 标识 | 含义 |
|---|---|
| `init` | WS 注册成功，连接就绪 |
| `user: xxx, msg: yyy` | 收到买家消息 |
| `AI reply: zzz` | AI 回复内容 |
| `[HUMAN] 店主介入会话 xxx` | 店主发消息，该会话后续 AI 静默 |
| `[SKIP] 会话 xxx 已人工介入` | 跳过 AI（人工接管中） |
| `ask/send error: ...` | AI 调用或消息发送失败 |
| `Cookie 失效` | token 过期，已推送飞书通知 |

---

## 十、依赖安装

```bash
pip install -r requirements.txt
```

`requirements.txt` 包含：`requests` `loguru` `websockets` `PyExecJS` `blackboxprotobuf` `pydantic` `typing_extensions` `openai` `python-dotenv` `qrcode` `pillow`
