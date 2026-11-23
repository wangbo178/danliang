# danliang

单量预测系统，包含 Python 预测服务、Vue 前端和桌面启动程序。

## 安装与运行

1. 创建 Python 虚拟环境，安装 `ecom_forecast/requirements.txt`、`ecom_forecast/requirements_api.txt` 和 `requirements_desktop.txt` 中的依赖。
2. 在 `frontend` 目录执行 `npm install`。
3. 根据 `.env.example` 创建本机 `.env` 并填写数据库连接信息。
4. 使用 `启动桌面程序.bat` 启动，或使用 `预测模型开发模式启动.bat` 进入开发模式。

详细说明见 `桌面程序说明.txt`、`数据库直连说明.txt` 和 `ecom_forecast/docs/`。

本机环境、密钥、数据库、日志及构建产物通过 `.gitignore` 排除。
