# MAIC
多智能体互动课堂

## 发布前打包流程

> 本流程供后续正式发布使用；日常开发仍按 `npm run dev` + `python main.py` 启动即可。
> 发布包 = 前端静态产物 + 后端源码与依赖清单 + 配置模板 + Nginx 配置。
> `.env`（含密钥）、`outputs/`（数据库与课件产物）都**不要**打进发布包。

### 1. 打包前检查

- 代码已提交，工作区无未保存改动。
- 新增环境变量时，同步更新 `server/.env.example`。
- 后端新增依赖时，同步更新 `server/requirements.txt`。
- 前端 API 地址保持 `/api`（同源**反向代理**：浏览器访问前端域名，Nginx 把 `/api` 转发到后端），不要写死后端域名；播放器需要读取课件 iframe 内部文档，依赖**同源策略**下的同域访问。

### 2. 前端构建（项目根目录）

```powershell
npm ci
npm run lint
npm run build
```

产物位于 `dist/`，其中 `API_BASE` 固定为 `/api`，由部署时的 Nginx 转发。

### 3. 后端检查（server 目录）

```powershell
python -m compileall -q app main.py
```

目标机器用虚拟环境安装依赖：`pip install -r requirements.txt`。运行期产生的 `outputs/` 和本地 `.env` 不进入发布包。

### 4. 组装发布包

建议目录结构：

```
maic-release/
├── dist/                       # 前端静态产物
├── server/                     # 后端代码
│   ├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── start_prod.ps1
│   └── start_prod.sh
└── deploy/
    └── nginx.conf.example      # 反代示例（含 SSE 关缓冲配置）
```

打包时排除：`node_modules/`、`server/outputs/`、`server/.env`、`__pycache__/`、`*.pyc`、`.git/`、`.codebuddy/`。

### 5. 目标机器配置

- 按 `server/.env.example` 注入配置（密钥建议由环境变量/密钥管理服务提供）：`DEBUG=false`、`CORS_ORIGINS` 填线上前端域名、`MAIC_OUTPUT_DIR` 指向**持久化目录**（重启/重新部署后仍保留的磁盘位置）。
- 复制 `deploy/nginx.conf.example`，修改 `server_name`、`root` 和后端地址；`/api` 必须反代到后端且保持同域，并保留 `proxy_buffering off`（SSE，服务端流式推送进度，缓冲会导致进度不更新）。
- 用 `start_prod.ps1` / `start_prod.sh` 启动后端，保持单 worker；会话、检查点与任务表都在进程内存，多 worker 会导致确认/取消失效。

### 6. 发布前验收

- `GET /api/health` 返回正常。
- 生成一条课件 → 点击「开始学习」→ 课件打开、翻页与音频正常。
- 课件列表、生成记录能打开对应课件。
- 大纲确认、中断生成、失败页重试可用。
- 重启后端后，历史课件与数据库仍在（验证持久化目录配置正确）。
- 浏览器控制台无跨域或资源 404 报错。

## React 编译器

本模板未启用 React 编译器，因为它会影响开发与构建性能。如需启用，请参阅[此文档](https://react.dev/learn/react-compiler/installation)。

## 扩展 ESLint 配置

如果你在开发生产环境的应用，建议更新配置以启用类型感知的 lint 规则：

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // 其他配置...

      // 移除 tseslint.configs.recommended 并替换为下面这行
      tseslint.configs.recommendedTypeChecked,
      // 或者，使用以下配置以获得更严格的规则
      tseslint.configs.strictTypeChecked,
      // 可选：添加以下配置以获得风格类规则
      tseslint.configs.stylisticTypeChecked,

      // 其他配置...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // 其他选项...
    },
  },
])

```

你也可以安装 [eslint-plugin-react-x](https://npmx.dev/package/eslint-plugin-react-x) 和 [eslint-plugin-react-dom](https://npmx.dev/package/eslint-plugin-react-dom) 来获取 React 专用的 lint 规则：

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // 其他配置...
      // 启用 React 的 lint 规则
      reactX.configs['recommended-typescript'],
      // 启用 React DOM 的 lint 规则
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // 其他选项...
    },
  },
])

```
