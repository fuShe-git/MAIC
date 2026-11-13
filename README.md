# MAIC
多智能体互动课堂

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
