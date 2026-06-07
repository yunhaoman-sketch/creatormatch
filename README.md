# CreatorMatch · AI海外达人精准匹配平台

> **帮助出海品牌快速找到 TikTok达人、Instagram达人、小红书达人，AI智能匹配 + 水军风险检测 + 一键生成合作话术**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

---

## 🚀 在线演示

- **前端（Vercel）**：https://creatormatch.vercel.app  *(部署后更新)*
- **后端 API（Render）**：https://creatormatch-api.onrender.com  *(部署后更新)*

---

## ✨ 功能特性

- 🔍 **AI 智能搜索**：自然语言描述产品，AI 自动解析意图并匹配最合适的达人
- 📊 **三平台覆盖**：TikTok、Instagram、小红书跨平台对比
- 🎯 **水军风险检测**：评论/点赞比、互动率多维度分析，识别异常账号
- 💬 **多语言话术生成**：中英文 × Professional/Friendly/Direct Deal 6套模板
- 📧 **邮件直发**：SMTP 配置（Gmail/Outlook/QQ/163），一键发送合作邀约
- 📋 **候选列表管理**：批量添加、批量生成话术、发送状态追踪

---

## 🛠 本地开发

### 1. 启动后端（Flask API）

```bash
cd backend
pip install -r requirements.txt
python app.py
# 后端运行于 http://localhost:5000
```

### 2. 打开前端

直接用浏览器打开 `frontend/index.html` 即可（无需构建步骤）。

> 若后端未启动，前端会自动使用内置 fallback 模拟数据。

### 3. 配置 LLM（可选）

```bash
# 设置环境变量以启用 AI 解析（否则使用规则引擎）
export LLM_API_KEY=sk-xxx           # OpenAI 或兼容 API Key
export LLM_API_BASE=https://api.openai.com/v1   # 可替换为第三方代理
```

---

## 🌐 部署到生产环境

### 方式一：Render（后端）+ Vercel（前端）（推荐）

#### 后端 → Render

1. 登录 [render.com](https://render.com)，点击 **New → Web Service**
2. 连接此 GitHub 仓库
3. 配置：
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
4. 添加环境变量：
   - `LLM_API_KEY` = 你的 API Key
   - `FRONTEND_ORIGIN` = 你的 Vercel URL（如 `https://creatormatch.vercel.app`）
5. 点击 **Create Web Service**，等待部署完成
6. 记录 Render 分配的 URL（如 `https://creatormatch-api.onrender.com`）

#### 前端 → Vercel

1. 登录 [vercel.com](https://vercel.com)，点击 **New Project**
2. 导入此 GitHub 仓库
3. 配置：
   - **Root Directory**: `frontend`（或使用根目录，vercel.json 已配置）
   - Framework Preset: **Other**
4. 部署完成后，**在 `frontend/index.html` 中更新 Render URL**：
   ```js
   // 找到这一行并替换为你的实际 Render URL：
   return 'https://creatormatch-api.onrender.com/api';
   ```
5. 推送更新，Vercel 自动重新部署

#### 绑定自定义域名（可选）

- Vercel：Project → Settings → Domains → Add Domain
- Render：Service → Settings → Custom Domain

---

### 方式二：Railway（后端）+ Netlify（前端）

#### 后端 → Railway

1. 登录 [railway.app](https://railway.app)
2. New Project → Deploy from GitHub Repo
3. 选择 `backend` 目录，设置环境变量
4. Railway 会自动检测 Procfile 并部署

#### 前端 → Netlify

1. 登录 [netlify.com](https://netlify.com)
2. Add new site → Import from Git
3. Build settings: **Publish directory** 设为 `frontend`
4. 部署完成

---

## 📁 项目结构

```
creatormatch/
├── backend/
│   ├── app.py              # Flask API（1000+ 行）
│   ├── data.py             # 模拟达人数据库（30位达人）
│   ├── requirements.txt    # Python 依赖
│   ├── Procfile            # Render/Railway 启动命令
│   └── runtime.txt         # Python 版本声明
├── frontend/
│   ├── index.html          # 完整 Vue 3 应用（单文件，无需构建）
│   └── _redirects          # Netlify SPA 路由
├── render.yaml             # Render 一键部署配置
├── vercel.json             # Vercel 部署配置
├── .gitignore
└── README.md
```

---

## 🔌 API 接口文档

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/search` | AI 匹配达人 |
| POST | `/api/outreach` | 生成合作话术（支持语言/语气/额外要求） |
| GET | `/api/candidates` | 获取候选列表 |
| POST | `/api/candidates` | 加入候选列表 |
| DELETE | `/api/candidates/:id` | 移除候选 |
| GET | `/api/stats` | 数据库统计 |
| POST | `/api/email/test` | 测试 SMTP 配置 |
| POST | `/api/email/send` | 发送合作邮件（含附件） |

---

## 🔐 环境变量说明

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `LLM_API_KEY` | 否 | OpenAI/兼容 API Key，未设置则使用规则引擎 |
| `LLM_API_BASE` | 否 | API 端点，默认 `https://api.openai.com/v1` |
| `FRONTEND_ORIGIN` | 否 | 前端域名，用于精确 CORS 控制（未设置则允许所有来源） |
| `PORT` | 否 | 由 Render/Railway 自动注入 |

---

## 📄 License

MIT © 2026 CreatorMatch
