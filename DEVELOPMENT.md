# 开发部署指南

`main` 面向 AstrBot，旧的独立 Web 管理服务保留在 `master`。

## 环境

- 使用支持 Plugin Pages 和 `astrbot.api.web` 的 AstrBot 环境，安装 `requirements.txt`。
- 前端使用 Node.js 22.12+ 和 npm；依赖版本由 `frontend/package-lock.json` 锁定。

## 构建与联调

1. 在 `frontend/` 执行 `npm ci`，然后执行 `npm run build`。
2. 构建输出为 `pages/settings/index.html` 和相邻静态资源，发布插件时一起携带。
3. 将插件安装到 AstrBot，启用或重载，从插件详情的「配置管理」打开。
4. 修改前端后重新构建并刷新 Page；修改后端后重载插件。

页面依赖 AstrBot 注入的 bridge，直接打开 Vite 开发地址不能读写配置。使用 hash 路由和相对资源路径，刷新子页面不会请求不存在的服务端路径。主题由宿主同步，不使用浏览器存储保存登录或侧栏状态。

## 后端约定

`api.PageAPI` 注册 `/{插件名}/{资源}` GET 和 `/{插件名}/{资源}/save` POST。资源固定为 config、apis、schedules、groups、auth；使用 AstrBot Dashboard 的认证和 `astrbot.api.web` 的请求、响应接口。

数据目录和 JSON 格式沿用旧版。写入采用临时文件替换，保存后清理对应缓存；接口定义变化时自动重载插件，计划任务保存后刷新调度。旧端口与登录哈希不再生效，磁盘值保留用于回退。

## 针对性检查

运行 `python -m unittest discover -s tests -v`（仅需标准库，模拟宿主和调度器边界），并在 `frontend/` 执行 `npm run build`。上线前在实际 Dashboard 检查页面入口、切换主题、各配置页保存及插件重载。
