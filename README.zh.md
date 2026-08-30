# 💡 关于 Staff Studio

Staff Studio是一套面向企业的数字员工构建与管理平台，帮助专业员工将工作经验、业务流程和判断标准固化为可以持续工作的数字员工，接手重复性任务，并将个人能力沉淀为可复用、可迭代、可追溯的组织资产。

## 核心亮点

- 🧑‍💼 **数字员工构建与管理**：将专业员工的经验、流程和判断标准固化为拥有岗位、工号、能力档案和工作记录的数字员工；支持能力成长、权限隔离及发布复用。
- 🧩 **状态机驱动的流程型技能**：通过自然语言生成结构化 SOP，以状态机保证复杂流程准确执行；支持多个流程实时切换、上下文保留、可视化编辑、版本管理和分支演化。
- 📚 **文档结构感知的知识检索**：基于文档、章节、页面和摘要等层级构建可导航索引，让数字员工先判断信息可能位于哪里，再逐层定位原文；支持知识分桶、定向检索、来源引用和检索调试。
- 🔌 **自主执行与持续迭代**：通过 HTTP API、MCP 和定时任务执行真实业务操作，并结合长期记忆、完整 Trace、真人接管、用户反馈和反馈分析形成持续迭代闭环。

## 目录

- [💡 关于 Staff Studio](#-关于-staff-studio)
  - [核心亮点](#核心亮点)
  - [Agent 一键部署](#agent-一键部署)
  - [目录](#目录)
  - [快速开始](#快速开始)
    - [环境要求](#环境要求)
    - [1. 克隆并安装](#1-克隆并安装)
    - [2. 配置模型](#2-配置模型)
    - [3. 启动 Web Demo](#3-启动-web-demo)
    - [4. 验证安装](#4-验证安装)
    - [常用命令](#常用命令)
      - [统一 Python 入口](#统一-python-入口)
  - [核心流程](#核心流程)
  - [渠道接入（微信 / 企业微信）](#渠道接入微信--企业微信)
  - [开放 API](#开放-api)
  - [项目结构](#项目结构)
  - [常见问题](#常见问题)
  - [风险与限制](#风险与限制)
  - [许可证](#许可证)
  - [致谢](#致谢)

## 快速开始

### 环境要求

- 支持 macOS、Linux、WSL 或 Windows PowerShell
- Python **3.11+**
- Node.js **20+** 与 npm
- OpenAI Chat Completions 兼容的模型接口和 API Key
- 应用本身不要求 CUDA；硬件要求由所选择的模型服务决定

### 1. 克隆并安装

首先克隆仓库：

```bash
git clone https://github.com/OpenBMB/StaffDeck.git
cd StaffDeck
```

macOS、Linux 或 WSL：

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e "backend[dev]"
npm --prefix frontend-enterprise ci
cp backend/.env.example backend/.env
```

Windows PowerShell：

```powershell
py -3 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
npm --prefix frontend-enterprise ci
Copy-Item backend\.env.example backend\.env
```

### 2. 配置模型

首次启动前编辑 `backend/.env`：

```dotenv
APP_SECRET="请替换为足够长的随机字符串"
DEMO_MODEL_BASE_URL="https://你的OpenAI兼容接口/v1"
DEMO_MODEL_NAME="你的模型名"
DEMO_MODEL_API_KEY="你的API-Key"
```

API Key 用于创建初始模型配置，存入数据库前会被加密。请勿提交 `backend/.env`。服务启动后也可以在**管理员 → 模型配置**中管理模型服务。

### 3. 启动 Web Demo

| 平台                | 推荐命令                          |
| ------------------- | --------------------------------- |
| macOS、Linux 或 WSL | `scripts/dev_up.sh --detach`    |
| Windows PowerShell  | `.\scripts\dev_up.ps1 --detach` |

两套包装脚本最终都会调用同一个跨平台 Python 生命周期入口 `scripts/dev.py`。启动过程会构建 Staff Studio 前端，并由一个 FastAPI 进程在 `5173` 端口同时提供 UI、API 与 Swagger 文档。默认管理员账号为 `admin` / `admin`，请在首次登录后通过账号配置修改密码。

### 4. 验证安装

macOS、Linux 或 WSL：

```bash
curl http://127.0.0.1:5173/api/health
```

Windows PowerShell：

```powershell
curl.exe http://127.0.0.1:5173/api/health
```

预期输出：

```json
{"status":"ok"}
```

打开 [http://127.0.0.1:5173/workspace/gallery](http://127.0.0.1:5173/workspace/gallery)，选择一个数字员工并发送首条消息。回答和执行记录应该在同一个对话轮次中流式显示。

### 常用命令

| 操作         | macOS、Linux 或 WSL            | Windows PowerShell                |
| ------------ | ------------------------------ | --------------------------------- |
| 后台启动     | `scripts/dev_up.sh --detach` | `.\scripts\dev_up.ps1 --detach` |
| 前台启动     | `scripts/dev_up.sh`          | `.\scripts\dev_up.ps1`          |
| 查看服务状态 | `scripts/dev_status.sh`      | `.\scripts\dev_status.ps1`      |
| 停止本地服务 | `scripts/dev_down.sh`        | `.\scripts\dev_down.ps1`        |

#### 统一 Python 入口

上述包装脚本最终都会调用 `scripts/dev.py`。也可以直接使用第 1 步创建的项目虚拟环境，避免依赖 Shell 脚本执行能力或系统 Python Launcher：

| 平台                | 直接后台启动                                                      |
| ------------------- | ----------------------------------------------------------------- |
| macOS、Linux 或 WSL | `backend/.venv/bin/python scripts/dev.py up --detach`           |
| Windows PowerShell  | `.\backend\.venv\Scripts\python.exe scripts\dev.py up --detach` |

需要执行其他操作时，将 `up --detach` 替换为对应的生命周期参数：

| 操作         | 参数            |
| ------------ | --------------- |
| 后台启动     | `up --detach` |
| 前台启动     | `up`          |
| 查看服务状态 | `status`      |
| 停止本地服务 | `down`        |

> 完整说明 → [Staff Studio 使用教程](https://staffdeck.openbmb.cn/#/docs/introduce?lang=zh)

## 核心流程

1. **创建数字员工**：设置职位、岗位边界、服务风格、创建者与访问范围。
2. **配置员工能力**：从广场复制或自行创建知识库、通用技能、SOP 与工具，不修改广场原件。
3. **发起会话**：从数字员工广场或员工列表进入；发送首条消息后持久化正式 Session。
4. **执行并观测**：在执行记录中查看流式意图、检索、技能、工具、校验和回答事件。
5. **必要时介入**：继续排队请求、取消运行、转人工或处理待回答内容。
6. **持续运营**：利用记忆、反馈、对话日志和定时任务长期优化员工能力。

## 渠道接入（微信 / 企业微信）

数字员工可以通过 IM 渠道直接对外服务：用户在微信或企业微信里与数字员工对话，渠道侧的多员工调度、意图自动分发、身份合并与对话观测全部由平台内置完成。渠道内核为渠道无关设计（适配器注册表），后续接入新渠道只需新增适配器。

**支持能力**

- 一个渠道账号挂载多个数字员工；`/员工`、`/切换 <名字>`、`/当前`、`/帮助` 指令调度；
- 意图自动分发：按用户消息意图（LLM 分类）自动路由到最合适的员工，SOP 进行中提高切换阈值，人工接管与手动切换保护窗内保持粘性；
- 身份合并：微信/企微用户可通过 `/绑定 <一次性码>` 把渠道身份合并到既有 Staff Studio 账号，记忆与会话统一，`/解绑` 可逆；
- 对话记录与投递日志按天归纳分页；管理员与员工创建者可按权限查看全部渠道会话；
- 可靠性：入站幂等去重、崩溃恢复、出站退避重试、token 失效自动告警与微信会话自愈。

**微信（个人，iLink 协议）**

1. 侧边栏进入「渠道接入」→「接入渠道」→ 选择「微信」并选择默认员工；
2. 详情页点击「扫码接入」，用手机微信扫描并确认（二维码约 2 分钟内有效，过期自动刷新）；
3. 微信用户对绑定后的微信号发消息即可对话（私聊或拉群）。

**企业微信（智能机器人 WS 长连接）**

1. 企业微信管理后台 → 应用管理 → 智能机器人，创建机器人并获取「机器人 ID」与「Secret」;
2. 「渠道接入」→「接入渠道」→ 选择「企业微信」,选择默认员工后创建；
3. 详情页填入机器人 ID 与 Secret 保存（可选填「企业 ID」，用于跨企业区分相同 userid，建议填写）;
4. 凭证保存后自动建立长连接，状态变为「已连接」即可收发消息。

**生产部署清单**

- 必须将 `APP_SECRET` 改为强随机值（渠道凭证加密密钥由它派生；更推荐同时配置独立的 `CHANNEL_SECRET`);
- 渠道凭证（bot token / secret)Fernet 加密落库，任何接口不回传明文；
- 绑定管理权限：管理员或绑定创建者；员工挂载动作本身即"对该渠道全部用户开放该员工",请按需授权。

## 开放 API

外部业务系统可以通过员工级 API Key 调用数字员工、持续会话、Harness v2 Run、SOP、知识、技能、工具和定时任务。完整的鉴权边界、接口清单、SSE、Webhook 与调用示例见 [数字员工开放 API v1](docs/open-api-v1.md)。

## 项目结构

```text
StaffStudio/
├── backend/                  # FastAPI 接口、Agent 运行时、存储与任务 Worker
├── frontend-enterprise/      # React/TypeScript Staff Studio 工作台
├── docs/                     # 教程、API、Schema 与示例流程
├── scripts/                  # 单端口服务生命周期与校验脚本
├── packaging/                # macOS、Linux 与 Windows 打包资源
├── README.md                 # English
└── README.zh.md              # 简体中文
```

## 常见问题

<details>
<summary><strong>页面可以打开，但数字员工不回答。</strong></summary>

检查所选模型配置、API Key、模型名和模型服务网络。随后查看执行记录与 `.dev/logs/app.log`，定位模型服务返回的具体错误。

</details>

<details>
<summary><strong>没有本地 GPU 可以运行吗？</strong></summary>

可以。应用调用 OpenAI 兼容模型接口，GPU 要求由你自行部署或使用的模型服务决定。

</details>

<details>
<summary><strong>为什么普通用户可以使用广场资源，但不能编辑？</strong></summary>

广场资源是可复用模板。普通用户可将有权限的资源复制或绑定到自己的员工，原始资源仍由创建者与管理员权限保护。

</details>

## 风险与限制

- 模型回答可能不正确、不完整或不一致；执行记录可以提高可审计性，但不能保证结论正确。
- 知识检索效果受原始文档质量、解析、索引、权限与模型能力共同影响。
- 外部工具与生成的 Runner 可能产生真实副作用。应使用最小权限凭据，并为高风险动作配置人工审批。
- 定时任务依赖持续运行的 Worker 与正确的用户时区设置。
- 本项目不能替代法律、医疗、金融、安全及其他受监管领域的专业审核。
- 未获得适当授权、隐私保护与人工监督时，不得使用本平台处理数据或自动作出重要决定。

## 许可证

本项目基于 GNU Affero General Public License v3.0 开源。

## 致谢

Staff Studio 由 [OpenBMB](https://www.openbmb.cn/) 生态孵化。
