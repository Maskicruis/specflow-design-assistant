# 部署、迁移与更新

## 1. 两种安装方式

推荐 **Git 克隆**，方便后续增量更新。仓库地址：

```text
https://github.com/Maskicruis/specflow-design-assistant
```

仓库为私有，需要使用有权限的 GitHub 账号；其他账号需由仓库所有者在 GitHub 添加访问权限。不应为了下载而把密钥放进克隆 URL。身份验证参考 [GitHub 官方克隆说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)。

也可以从 GitHub 下载源代码 ZIP，但 ZIP 不带 Git 历史，不能运行自动更新脚本；后续需下载新版本并迁移本地资料。因此需要持续更新时应使用 Git。

GitHub 在此负责代码托管和版本发布，不托管正在运行的后端。这套 Python 服务不能仅通过 GitHub Pages 运行。

## 2. Windows 安装

安装 [Python 3.13（64 位）](https://www.python.org/downloads/) 和 [Git for Windows](https://git-scm.com/downloads/win)。安装 Python 时启用 Python Launcher 或加入 PATH。建议为源码、规范、图片缓存及备份准备充足磁盘空间；处理量随资料规模而变化。

在计划保存程序的目录打开 PowerShell：

```powershell
git clone https://github.com/Maskicruis/specflow-design-assistant.git
cd specflow-design-assistant
.\安装依赖.cmd
Copy-Item .env.example .env
```

`.env` 只在第一次安装时创建。已有该文件时不要覆盖。用文本编辑器打开 `.env`，将自己的密钥填入 `DEEPSEEK_API_KEY=` 后面，保存为 UTF-8。不要将密钥发到聊天、截图或 GitHub。

双击 `启动演示.cmd` 或 `启动演示.vbs`。进入 <http://127.0.0.1:8765> 的「模型与连接」：

| 设置项 | 填写方式 |
|---|---|
| 服务基础地址 | 实际提供 Chat Completions 兼容接口的地址，例如 `https://api.deepseek.com` |
| 回答模型 | 账号可用的文本模型名称；内置配置为 `deepseek-v4-flash` |
| 视觉模型 | 支持图片输入的模型名称；内置配置为 `deepseek-v4-flash-vision-exp` |
| 密钥环境变量名 | 新安装填写 `DEEPSEEK_API_KEY`，这里不能填写密钥值 |
| 启用模型、允许远程发送 | 阅读数据发送提示，确认规范允许外发后再勾选 |

内置模型名称只是当前适配配置，不保证每个账号都可用，也不是长期可用性承诺。文本连接测试成功只验证文本接口；视觉能力需要用少量页面验证。首次建议只解析 1–2 页，核对质量和费用后再扩大批次。

`.env` 在服务启动时读取，修改后要停止并重新启动服务。系统已有同名环境变量时，系统值优先。程序不会把密钥写入 SQLite 或发送给网页。

没有密钥时，可以关闭模型连接并使用已经解析内容的本地检索。新导入扫描页需等待可用视觉模型，不会自动生成假内容。也可以配置本机兼容服务，例如 `http://127.0.0.1:1234/v1`；实际兼容性取决于其图片、JSON 模式和 token 参数支持。项目本身不打包模型权重。

## 3. 迁移当前电脑的全部资料

只复制代码不能带走知识库；只复制 SQLite 也不能带走原文和图片。

旧电脑：

1. 在「处理任务」停止视觉批次并等待当前请求和文件导入结束，避免维护时丢失进行中的操作。
2. 暂停查询，双击 `备份资料.cmd`。它会停止本目录的空闲服务，再生成 `work/backups/specflow-data-时间.zip`。
3. 通过自己的存储设备或受控文件传输，把这个备份交给新电脑。**备份包含规范原件、解析结果、历史和收藏，不上传到代码仓库或 GitHub Release。**

新电脑：

1. 完成代码克隆和依赖安装，不复制旧电脑的 `.venv`。
2. 停止新电脑上的服务。在一个新建、空资料库的安装目录中解压备份，使 `data` 和 `规范文件` 与 `app` 同级。不要覆盖另一套正在使用的资料；先另外备份，当前不支持两套数据库合并。
3. 单独配置新电脑的 `.env`。备份包含模型设置中的“环境变量名”，如果旧配置不是 `DEEPSEEK_API_KEY`，启动后在设置页面改为新变量名，或者在本机配置同名变量。
4. 检查原文是否齐全，再启动：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe launcher.py
```

`missing_originals` 为空表示索引指向的原文件都存在。成功恢复后，无须重新视觉解析已完成页面。旧版绝对路径会在启动时尝试迁移，仅在文件 SHA-256 匹配时更新；不能匹配时保留原记录，提示补充文件，不随意绑定同名资料。

如果使用自定义 `SPEC_DATA_DIR` / `SPEC_SOURCES_DIR`，备份仍归一化为 `data/` 和 `规范文件/`。恢复时可使用默认路径，也可把这两个目录分别放入新配置指定的路径。相对配置路径以项目根目录为基准。

不使用脚本时，须先停止服务，再完整复制 `data` 和 `规范文件`。不要在 SQLite 正在写入时只拷贝主数据库文件而漏掉 WAL。

## 4. 后续更新

Git 安装版本双击 `更新程序.cmd`，或执行：

```powershell
.\.venv\Scripts\python.exe manage.py update
```

脚本依次检查本地源码修改、停止没有解析任务的本目录服务、生成私人资料备份、运行 `git pull --ff-only`、安装依赖。不会强制覆盖改动，也不会推送本地数据。

更新完成后双击启动入口。代码拉取失败时，原资料和备份保留，先排查 GitHub 登录及网络；依赖安装失败时，重试安装后再启动。脚本不保证跨任意未来版本的自动回滚，重大数据库升级应遵循当时的版本说明。

私人资料、日志、密钥和虚拟环境已被 `.gitignore` 排除。不要使用 `git add -f` 强行上传它们。备份会占用磁盘，按自己的留存要求定期整理。

## 5. 命令与故障排查

```powershell
# 停止服务（请先停止解析并结束当前查询）
.\.venv\Scripts\python.exe manage.py stop
# 服务停止后单独备份
.\.venv\Scripts\python.exe manage.py backup
# 开发验证，不需要模型密钥
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

| 现象 | 检查 |
|---|---|
| 克隆提示 Repository not found | 仓库为私有，确认当前 GitHub 身份有访问权限 |
| 找不到 Python 3.13 | 安装对应版本，重新打开终端；可手工运行 `python -m venv .venv` |
| 服务无法启动 | 查看 `work/server-error.log`；确认安装依赖成功、8765 端口未被其他安装占用 |
| 已有旧版服务占用端口 | 停止旧目录对应的 Python 服务。新启动器不会自动结束其他安装 |
| 未检测到密钥 | `.env` 文件名和变量名必须一致，确认已重启；不要误存为 `.env.txt` |
| 模型 401 / 403 / 404 / 余额错误 | 核对账号权限、密钥、基础地址、模型名和计费；不要把完整密钥贴进报错记录 |
| 解析卡住或失败 | 查看处理任务日志，先小批次验证，成功页可保留并续跑 |
| 原页缺失 | 运行 `manage.py check`，同时迁移规范原件和 `data/imports` |
| 更新被拒绝 | 先保存自己的源码修改；不要用强制重置来覆盖不理解的变更 |

关闭网页不会停止服务。维护前应主动停止解析、结束查询。Windows 停止命令会结束此安装的服务进程，尚未完成的请求可能没有保存；已提交的 SQLite 内容保留。

## 6. Linux / macOS 参考

以下使用 Python 3.13。核心代码使用跨平台路径，Windows 双击脚本不适用于这些系统。Linux 的实际 CI 结果见仓库 Actions；未做 macOS 桌面验证。

```bash
git clone https://github.com/Maskicruis/specflow-design-assistant.git
cd specflow-design-assistant
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 后：
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

命令行服务使用 Ctrl+C 停止；后台启动可执行 `.venv/bin/python launcher.py --no-browser`。仍然只在本机浏览器访问，不将本演示直接作为公网或团队共享服务。
