# ProxyCanvas

ProxyCanvas 是一个本地优先的 AI 图片工作流项目，用一个统一的 Web 工作台聚合多个图像生成后端。

它不是另一个反代服务本体，而是放在 APIMart、ChatGPT2API、CLIProxyAPI、Nanobanana2、Sousaku、LumaLabs 等服务前面的图片生成控制台：负责提交任务、管理参考图、保存结果、本地图廊、账号状态、任务记录和日常运维。

- `backend_v2/`：Python Flask 后端，负责图片接口、provider 适配、任务系统、账号配置和本地数据读写
- `frontend_v2/`：Vite + React 前端，负责图片工作台、图廊、任务页和账号页
- `config/`：本地 provider 配置、token 配置和账号缓存
- `sdk/`：Sousaku、LumaLabs 等 provider 的本地 SDK 封装

前端开发服务器通过 Vite 代理访问后端；生成图片、任务数据、缩略图和 SQLite 数据库默认保存在本机。

## 核心功能

- 聚合多种图片生成后端：APIMart、ChatGPT2API、CLIProxyAPI、Nanobanana2、Sousaku、LumaLabs
- 支持文本生图、参考图生成、连续编辑、蒙版/选区编辑等图片工作流
- 支持本地图廊：图片导入、收藏、标签、Prompt 元数据、缩略图缓存和结果复用
- 支持任务记录：后台任务、状态轮询、错误信息、结果路径和任务详情
- 支持账号运维：Sousaku token 导入、账号刷新、启用/禁用、删除和余额/额度展示
- 支持多 provider 配置：API key、base URL、保存目录、代理、并发限制、模型 credit 估算
- 支持 SQLite 存储任务与图廊数据，适合长期本地使用
- 支持本地启动脚本，一次启动常用外部服务、后端和前端

## 图片工作台

ProxyCanvas 的图片工作台围绕日常生成流程设计：

- 在同一界面选择 provider、模型、比例、质量和参考图
- 查看任务状态、生成结果和错误信息
- 将结果图继续作为编辑源图
- 打开选区编辑器进行局部重绘
- 复制 Prompt、复用参考图、保存结果到本地图廊
- 在图廊中按日期、标签、收藏和导入来源管理图片

## 界面预览

| 图廊工作台 | 图片详情 |
| --- | --- |
| ![ProxyCanvas gallery UI](assets/web_ui.png) | ![ProxyCanvas image detail UI](assets/pic_ui.png) |

| 任务中心 | 账号池 |
| --- | --- |
| ![ProxyCanvas task UI](assets/task_ui.png) | ![ProxyCanvas account UI](assets/account_ui.png) |

## 支持的外部服务

ProxyCanvas 本身负责统一界面、任务管理和本地保存；真正的图片生成由外部 provider 完成。

不同 provider 能力不同：有的适合免费账号，有的支持更高分辨率，有的支持特定模型或账号池。ProxyCanvas 的作用是把这些生成来源接到同一个工作台里，方便按场景切换。

### APIMart

APIMart 是一个 API 中转站，可以接入多种图像模型。ProxyCanvas 当前主要用于调用 APIMart 的 GPT-IMAGE-2 和 Nanobanana 相关模型。

APIMart API Key 和 Base URL 在 `config/providers.json` 中配置，也可以在前端“全局设置 → Provider → APIMart”里修改：

```json
{
  "providers": {
    "apimart": {
      "baseUrl": "https://api.apimart.ai",
      "apiKey": "sk-your-token"
    }
  }
}
```

### ChatGPT2API

[ChatGPT2API](https://github.com/basketikun/chatgpt2api) 是一个本地反代工具，可以把 ChatGPT 网页侧能力封装成本地 OpenAI-compatible API。

在图片场景中，它主要用于 GPT-IMAGE-2 模型。当前适合 ChatGPT Free 账号使用，最大输出分辨率通常为 1080P。

如果 ChatGPT2API 运行在 `8000` 端口，可以在 `config/providers.json` 里配置：

```json
{
  "providers": {
    "openai": {
      "baseUrl": "http://127.0.0.1:8010/v1",
      "apiKey": "chatgpt2api",
      "defaultModel": "gpt-image-2"
    }
  }
}
```

### CLIProxyAPI

[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 可以把 Gemini CLI、Antigravity、ChatGPT Codex、Claude Code、Grok Build 等工具聚合为标准兼容接口。

在 ProxyCanvas 的图片场景中，CLIProxyAPI 使用的是 Codex 侧的 GPT-IMAGE-2 能力。它可以输出更高分辨率图片，最大可到 4K，但质量档位和审核参数通常不可设置，并且需要 ChatGPT Plus 会员账号。

CLIProxyAPI 的地址和 API Key 在 `config/providers.json` 中配置：

```json
{
  "providers": {
    "cliproxy": {
      "baseUrl": "http://127.0.0.1:8317/v1",
      "apiKey": "sk-your-token"
    }
  }
}
```

### Nanobanana2

Nanobanana2 用于接入 [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) 提供的 Nanobanana2 模型能力。

如果本地 Nanobanana2 服务运行在 `8045` 端口，可以在 `config/providers.json` 里配置。`baseUrl` 对应的是 Antigravity-Manager / Nanobanana2 服务地址和端口，请按实际端口修改：

```json
{
  "providers": {
    "nanobanana2": {
      "baseUrl": "http://127.0.0.1:8045",
      "apiKey": "sk-your-token"
    }
  }
}
```

### Sousaku

Sousaku 需要你自己的账号 token。ProxyCanvas 会读取 token、刷新账号信息，并在生成时按配置选择可用账号。

账号配置位于：

```text
config/sousaku_config.json
```

把自己的 token 填入 `tokens` 数组即可。真实 token 不要提交到仓库。

示例：

```json
{
  "tokens": [
    "your-sousaku-token",
    "your-second-sousaku-token"
  ],
  "save_dir": "data/sousaku",
  "accounts_path": "sousaku_accounts.json"
}
```

`tokens` 可以填一个或多个 Sousaku 账号 token。ProxyCanvas 会根据配置进行账号刷新和轮换。

Sousaku 的账号 token 仍由 `config/sousaku_config.json` 管理；模型、比例、分辨率和数量等前端可见参数由 `config/providers.json` 中的 `sousaku.models` 描述。

### LumaLabs

LumaLabs 需要你自己的临时 `wos_session`。这个值来自网页会话，等同于账号凭据，可能会过期。

相关配置位于：

```text
config/lumalabs_config.json
```

`wos_session` 这类 web session cookie 请当作密钥处理，不要提交到仓库。

## 仓库结构

```text
.
├── assets/
│   ├── account_ui.png
│   ├── pic_ui.png
│   ├── task_ui.png
│   └── web_ui.png
├── backend_v2/
│   ├── routes/
│   ├── services/
│   │   └── jobs/
│   ├── app.py
│   ├── config.py
│   └── requirements.txt
├── config/
│   ├── app_settings.json
│   ├── providers.json
│   ├── lumalabs_config.example.json
│   ├── lumalabs_config.json
│   ├── sousaku_accounts.json
│   └── sousaku_config.json
├── frontend_v2/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── sdk/
│   ├── lumalabs/
│   └── sousaku/
├── server_port.json
├── start.bat
└── README.md
```

## 环境要求

- Python 3.10+
- Node.js 20+
- npm 10+
- Windows 本地开发环境优先，macOS / Linux 可手动启动前后端

如果你使用 `start.bat`，脚本会按文件顶部配置的路径启动 CLIProxyAPI、ChatGPT2API、ProxyCanvas 后端和前端；如果外部服务路径为空或不存在，会自动跳过对应服务。

## 获取项目

```powershell
git clone <your-repo-url>
cd ProxyCanvas
```

如果你是从本地 APIMart 副本整理出来的版本，直接进入项目目录即可：

```powershell
cd F:\CodeProject\ProxyCanvas
```

## 本地开发

安装后端依赖：

```powershell
cd backend_v2
pip install -r requirements.txt
```

安装前端依赖：

```powershell
cd frontend_v2
npm install
```

启动后端：

```powershell
cd backend_v2
python -u app.py
```

启动前端：

```powershell
cd frontend_v2
npm run dev
```

默认地址：

```text
前端: http://localhost:5380
后端: http://localhost:5700
```

健康检查和 API 路由由 Flask 后端提供，前端通过 Vite proxy 转发 `/api` 请求到后端。

## 一键启动

安装完后端和前端依赖以后，Windows 可以直接运行：

```text
start.bat
```

默认脚本会尝试启动：

- CLIProxyAPI，前提是你在脚本顶部配置了 `CLIPROXY_DIR`。有 `cli-proxy-api.exe` 时优先运行 exe，否则使用 `go run ./cmd/server`
- ChatGPT2API，前提是你在脚本顶部配置了 `CHATGPT2API_DIR`。本地开发建议显式指定端口，并让 `config/providers.json` 的 `baseUrl` 保持一致
- ProxyCanvas 后端
- ProxyCanvas 前端

`start.bat` 只负责启动服务，不会自动安装 Python 或 Node.js 依赖。首次使用请先按“本地开发”中的步骤执行 `pip install -r requirements.txt` 和 `npm install`。

你大概率需要根据自己的机器修改：

- CLIProxyAPI 所在目录
- ChatGPT2API 所在目录
- 可选 conda 环境名
- 前后端端口
- 浏览器启动方式

默认情况下，`start.bat` 不会启用 conda，会直接使用系统 PATH 里的 `python` 启动后端：

```bat
set "BACKEND_CONDA_ENV="
set "BACKEND_CMD=python -u app.py"
```

如果你的 Python 依赖装在 conda 环境里，需要自己填写环境名：

```bat
set "BACKEND_CONDA_ENV=my_env"
```

CLIProxyAPI / ChatGPT2API 的路径也是可选配置。示例：

```bat
set "CLIPROXY_DIR=D:\apps\CLIProxyAPI"
set "CLIPROXY_EXE=cli-proxy-api.exe"
set "CLIPROXY_CMD=go run ./cmd/server"
set "CHATGPT2API_DIR=D:\Code\chatgpt2api"
set "CHATGPT2API_CONDA_ENV=zimg"
set "CHATGPT2API_CMD=python -m uvicorn main:app --host 127.0.0.1 --port 8010 --no-access-log --log-level info"
```

如果 ChatGPT2API 使用别的 Python 环境，把 `CHATGPT2API_CONDA_ENV` 改成你自己的环境名；如果直接使用系统 Python，可以留空。若你的 ChatGPT2API 项目仍想使用项目默认端口，也可以改成：

```bat
set "CHATGPT2API_CMD=python -u main.py"
```

如果不配置外部服务路径，脚本会跳过对应服务，只启动 ProxyCanvas 后端和前端。

## 配置文件

主要配置文件：

- `config/app_settings.json`：应用运行设置，包括端口、图片保存路径、图廊偏好、代理、任务并发和高级参数
- `config/providers.json`：Provider 设置，包括 API Key、Base URL、模型列表、参数控件和能力描述
- `config/sousaku_config.json`：Sousaku token、模型 credit 估算和 token 轮换策略
- `server_port.json`：前后端共享端口配置，Vite 会读取它来代理后端 API
- `config/sousaku_accounts.json`：Sousaku 账号缓存
- `config/lumalabs_config.json`：LumaLabs session 账号配置
- `backend_v2/config.py`：默认值、路径常量和兼容导出；日常配置优先改 JSON 或前端设置页

常用配置项索引：

| 要配置的内容 | 配置位置 | 说明 |
| --- | --- | --- |
| 后端端口 | `config/app_settings.json` 的 `server.backendPort`，或设置页“全局设置 → 存储/高级”对应项 | 默认 `5700`。后端启动时如果端口被占用，会尝试自动换到可用端口并写入 `server_port.json`。 |
| 前端端口 | `config/app_settings.json` 的 `server.frontendPort`，以及 `server_port.json` | 默认 `5380`。Vite 会读取 `server_port.json` 决定开发服务器端口和后端代理端口。 |
| 图片保存路径 | `config/app_settings.json` 的 `storage.saveDir`，或前端设置页“存储” | 默认 `gallery`，也就是项目根目录下的 `gallery/`。目录会在生成、导入、缩略图缓存时自动创建。 |
| APIMart | `config/providers.json` 的 `providers.apimart.apiKey`、`providers.apimart.baseUrl` | `apiKey` 填 APIMart 后台获取的 key；`baseUrl` 默认 `https://api.apimart.ai`。 |
| ChatGPT2API / OpenAI-compatible | `config/providers.json` 的 `providers.openai.apiKey`、`providers.openai.baseUrl`、`providers.openai.defaultModel` | 指向本地或远程 OpenAI-compatible 图片接口，默认本地 `http://127.0.0.1:8010/v1`。 |
| CLIProxyAPI | `config/providers.json` 的 `providers.cliproxy.apiKey`、`providers.cliproxy.baseUrl` | 默认本地 `http://127.0.0.1:8317/v1`。 |
| Nanobanana2 | `config/providers.json` 的 `providers.nanobanana2.apiKey`、`providers.nanobanana2.baseUrl` | 默认本地 `http://127.0.0.1:8045`，对应 Antigravity-Manager 默认端口。 |
| Provider 模型和参数 | `config/providers.json` 的 `providers.<id>.models` | 控制前端模型下拉、比例、分辨率、质量、数量等参数选项。 |
| Sousaku token | `config/sousaku_config.json` 的 `tokens` | 可填一个或多个 token；账号刷新、轮换和 credit 阈值也在这个文件里配置。 |
| LumaLabs session | `config/lumalabs_config.json` | 可从 `config/lumalabs_config.example.json` 复制后填写 `wos_session` 等账号信息。 |
| 代理 | `config/app_settings.json` 的 `network.httpProxies` | 如果下载参考图或访问外部 API 需要代理，在这里设置；不需要代理可设为 `null`。 |
| 任务并发和超时 | `config/app_settings.json` 的 `jobs.maxWorkers`、`jobs.providerLimits`、`jobs.defaultTimeoutSeconds` | 控制后台任务 worker 数、不同 provider 的并发上限和任务超时时间。 |
| SQLite 数据库 | `backend_v2/config.py` 的 `JOBS_DB_PATH`、`GALLERY_DB_PATH` | 默认在项目根目录 `data/` 下保存任务和图廊元数据。 |
| 一键启动外部服务 | `start.bat` 顶部的 `CLIPROXY_DIR`、`CHATGPT2API_DIR`、`*_CONDA_ENV`、`*_CMD` | 只影响 Windows 一键启动脚本；不配置外部服务路径时会跳过对应服务。 |

常用 API Key 在 `config/providers.json` 中填写：

```json
{
  "providers": {
    "apimart": { "apiKey": "sk-your-token" },
    "openai": { "apiKey": "chatgpt2api" },
    "cliproxy": { "apiKey": "sk-your-token" },
    "nanobanana2": { "apiKey": "sk-your-token" }
  }
}
```

图片保存路径在 `config/app_settings.json` 中设置：

```json
{
  "storage": {
    "saveDir": "gallery"
  }
}
```

默认情况下，所有 provider 共用项目根目录下的 `gallery/` 作为图片根目录。导入图片会保存到 `gallery/imports/`，缩略图缓存会保存到 `gallery/thumbnails/`。

如果需要代理，可在 `config/app_settings.json` 中配置：

```json
{
  "network": {
    "httpProxies": {
      "http": "http://127.0.0.1:7890",
      "https": "http://127.0.0.1:7890"
    }
  }
}
```

## 数据存储

ProxyCanvas 当前主要使用本地文件和 SQLite：

- 任务数据库：`data/jobs.sqlite`
- 图廊数据库：`data/gallery.sqlite`
- Sousaku 账号缓存：`config/sousaku_accounts.json`
- LumaLabs 账号配置：`config/lumalabs_config.json`
- 生成图片：默认 `gallery/`
- 导入图片：默认 `gallery/imports/`
- 缩略图缓存：默认 `gallery/thumbnails/`
- 前端依赖：`frontend_v2/node_modules/`，不入库
- 前端构建产物：`frontend_v2/dist/`，不入库

默认运行数据不应该提交到 Git。

## 构建

构建前端：

```powershell
cd frontend_v2
npm run build
```

当前项目还不是单二进制交付形态。如果后续要做正式发布，可以考虑：

- 构建 `frontend_v2/dist`
- 让 Flask 后端托管静态资源
- 打包 Python 运行环境或提供 Docker 镜像
- 将示例配置和真实配置分离

## 检查

前端检查：

```powershell
cd frontend_v2
npm run lint
npm run build
```

后端基础检查：

```powershell
cd backend_v2
python -m py_compile app.py config.py
```

如果修改了 provider、任务系统或账号管理逻辑，建议手动验证：

- 生成任务提交
- 任务状态轮询
- 图片结果保存
- 图廊刷新
- Sousaku token 导入、刷新、禁用和删除

## 主要接口

常用接口按功能大致分为：

- 图片生成：`POST /api/generate`、`POST /api/generate-openai`、`POST /api/generate-cliproxy`、`POST /api/generate-nanobanana2`、`POST /api/generate-sousaku`
- 任务管理：`GET /api/jobs`、`POST /api/jobs`、`GET /api/jobs/<job_id>`、`POST /api/jobs/<job_id>/retry`、`POST /api/jobs/<job_id>/cancel`
- 图廊管理：`GET /api/gallery`、`POST /api/gallery`、`DELETE /api/gallery/<image_id>`、`POST /api/gallery/import`
- 本地文件：`GET /api/serve-image`、`GET /api/thumbnail`、`POST /api/open-folder`
- provider 能力：`GET /api/capabilities`
- provider 账号：`GET /api/provider-accounts`、`POST /api/provider-accounts/sousaku/tokens`、刷新、启用/禁用、删除 Sousaku 账号
- 上传与代理：`POST /api/upload-image`、`GET /api/proxy-image`、`POST /api/process-url`

具体接口以 `backend_v2/app.py` 和 `backend_v2/routes/` 中的实现为准。

## 本地数据与敏感信息

以下内容不要提交到 Git：

- 真实 API Key
- Sousaku token
- LumaLabs `wos_session`
- OpenAI-compatible API key
- 账号缓存中的真实账号信息
- `data/*.sqlite`
- 生成图片、导入图片、缩略图缓存
- `frontend_v2/node_modules/`
- `frontend_v2/dist/`
- 日志、临时文件和本地测试输出

如果密钥曾经被提交、截图或分享过，建议直接轮换或废弃，而不是只从文件中删除。

## 社区与上游

ProxyCanvas 适配和依赖多个外部项目或服务，包括但不限于：

- [ChatGPT2API](https://github.com/basketikun/chatgpt2api)
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
- [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager)

这些项目的行为、接口和可用性可能随时间变化。若上游变更，ProxyCanvas 的适配层也可能需要同步调整。

## 免责声明

本项目仅供个人学习、技术研究与非商业性技术交流使用。

严禁将本项目用于任何商业用途、盈利性使用、批量滥用、自动化滥用或规模化调用。

严禁将本项目用于生成、传播或协助生成违法、暴力、色情、未成年人相关内容，或用于诈骗、欺诈、骚扰等非法或不当用途。

严禁将本项目用于任何违反 OpenAI、APIMart、Sousaku、LumaLabs、ChatGPT2API、CLIProxyAPI、Antigravity-Manager 或其他相关平台服务条款、当地法律法规或平台规则的行为。

使用者应自行承担全部风险，包括但不限于账号被限制、临时封禁、永久封禁、额度损失、数据丢失以及因违规使用导致的法律责任。

使用本项目即视为你已充分理解并同意本免责声明全部内容；如因滥用、违规或违法使用造成任何后果，均由使用者自行承担。

> 重要提醒：本项目涉及多个第三方服务和本地反代工具。请勿使用自己的重要账号、常用账号或高价值账号进行高风险测试。

## License

MIT License
