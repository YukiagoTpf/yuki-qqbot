# yuki-qqbot

我的 QQ 群机器人：NapCat（QQ 协议端，个人号）+ AstrBot（插件框架），跑在 Mac 上 Colima 的 docker 里。这个仓库就是线上的部署目录：compose、运维脚本、自己写的插件都在这里；线上数据（`data/`）和密钥（`.env`）不进仓库，哪个群开了什么也只写在线上配置里。

## 功能

| 插件 | 干什么 |
|---|---|
| [猜题](https://github.com/YukiagoTpf/astrbot_plugin_guessless)（`plugins/astrbot_plugin_guessless`，子模块） | 九宫格看图猜游戏、动画、宝可梦、机体，看街景猜国家，听 OP 猜番，看 K 线猜涨跌，盲猜宝可梦、国家、城市……按群记排行，每小时限量。不需要 LLM |
| [杀炫区](plugins/astrbot_plugin_xuanqu) | 旁听群聊，用 LLM 认出炫耀恋爱、收入的发言（包括聊天记录截图、一天里的暗炫），禁言并损一句；群友可以 @bot 举证、喊冤 |
| [搜烧鸡](plugins/astrbot_plugin_xsearch) | 回复一张图 @bot，先以图搜图，再让本机的 Grok 去 X 上核实，找图的出处 |
| [群知识库](plugins/astrbot_plugin_groupkb) | 把群聊记到本地，每天凌晨让 LLM 读一遍前一天的聊天，把群友外号、群里的黑话和值得留档的事记进按群的知识库，给别的插件用 |
| [提issue](plugins/astrbot_plugin_issue) | 群友 @bot 提意见、报 bug，整理成猜题仓库的 issue；bot 管理员说「清issue」，本机开个 Claude Code 去处理 |

用法和设计见各插件目录的 README。

```
compose.yml          NapCat、AstrBot 两个服务；端口只绑 127.0.0.1
.env.example         拷成 .env 再填：UID/GID、NapCat WebUI token、bot 的 QQ 号、热重载开关
Makefile             运维命令，`make` 查看
groups.example.yaml  按群的知识库模板，见下文「按群配置」
plugins/             自己写的 AstrBot 插件，一个子目录一个插件；猜题是子模块
scripts/             OneBot 调试、掉线看门狗、宿主机上的三个桥、备份、提交前检查
data/                不进仓库：AstrBot 的配置、sqlite、插件数据，NapCat 的登录态（NapCat 也挂了 data/astrbot，用来读媒体文件）
backups/             不进仓库：make backup 的产物
```

## 首次使用

```bash
git clone --recursive https://github.com/YukiagoTpf/yuki-qqbot.git
cd yuki-qqbot
cp .env.example .env   # 按里面的注释填
make hooks             # 提交前自动跑 check-public，见下文「提交」
```

1. `make up`
2. `make qr` 打开二维码，用**小号**扫码登录（NapCat 是非官方协议，有封号风险）。
   也可以在 NapCat WebUI <http://127.0.0.1:6099/webui> 扫码，token 在 `.env`。
   登录后把 QQ 号填进 `.env` 的 `NAPCAT_ACCOUNT` 再 `make up`，之后重启走快速登录，不用再扫。
3. 打开 AstrBot WebUI <http://127.0.0.1:6185>。初始账号 `astrbot`，初始密码只在**第一次启动**时打印在日志里（`make logs s=astrbot`），登录后改掉。
4. AstrBot：`机器人 → 创建机器人 → OneBot v11`，反向 WebSocket 主机 `0.0.0.0`、端口 `6199`，token 留空。
   NapCat 首次启动已按 `MODE=astrbot` 生成了连 `ws://astrbot:6199/ws` 的配置，不用再配。
5. 日志里出现 `aiocqhttp(OneBot v11) 适配器已连接` 即通。再去 `模型提供商` 配模型（杀炫区、提issue 用 DeepSeek）。
6. 在 WebUI 的插件页给每个插件配上要生效的群和各自的 key，见下文。

## 按群配置

仓库里不写具体的群，都在线上配置里：

- **白名单**：只响应白名单里的群，其他群和私聊的消息在 AstrBot 流水线的白名单阶段就被丢弃，插件和 LLM 都看不到。
  - 配置位置：WebUI `配置 → 访问控制 → 白名单`，即 `data/astrbot/cmd_config.json` 的 `platform_settings`。
  - `id_whitelist` 填群号或完整会话 ID（如 `napcat:GroupMessage:<群号>`）；**空列表 = 全部放行**。
  - `wl_ignore_admin_on_group` / `wl_ignore_admin_on_friend` 关着，管理员也不能绕过白名单；想让自己私聊 bot，把自己 QQ 加进 `admins_id` 并打开后者。
  - `id_whitelist_log` 关着，否则其他群每条消息都刷一行日志。
  - `make groups` 查群号（走 NapCat 调试接口，不依赖 AstrBot）。同名群很多，按群号认。
  - 手改 `cmd_config.json` 要先 `docker compose stop astrbot`，改完再起，避免运行中被覆盖。文件是 UTF-8 BOM。
- **各插件开在哪些群**：每个插件配置里的 `groups`（WebUI 插件页，即 `data/astrbot/config/<插件>_config.json`）。猜题的每小时题数、宵禁、帮助里追加的别的插件用法也按群配。
- **按群的知识库**：`data/astrbot/groups.yaml`，模板是 `groups.example.yaml`。记这个群的黑话、规矩，群友除了群名片以外的外号，还有每天值得留档的事。群知识库插件每天凌晨自动往里加，只加不删；手改也行，改了不用重启。现在杀炫区在用：黑话交给判定的模型，外号用来认出打字里提到的是谁（别人的外号不会被当成说话的人自己）。

## 写插件

```bash
make new-plugin name=astrbot_plugin_foo   # 生成骨架并软链进 AstrBot，注册 /foo
```

- `plugins/<name>` 挂到容器的 `/AstrBot/local-plugins`，`data/astrbot/plugins/<name>` 是指过去的软链（宿主机上显示为断链，正常）。新增或删除插件目录后 `make link`（`make up` 会自动跑）。
- 软链插件不会被插件市场的同名插件覆盖。
- 改代码后：WebUI `插件` 页点重载；或在 `.env` 设 `ASTRBOT_RELOAD=1` 再 `make up`，保存即自动重载。热重载靠轮询，AstrBot 空载 CPU 会从约 0% 升到约 3%，写完记得关。
- 第三方依赖写进插件的 `requirements.txt`，加载时自动 pip 安装。
- 持久化数据写到 `data/` 下，别写在插件目录里。
- 文档就在镜像里：`docker exec astrbot ls /AstrBot/docs/zh/dev/star`，以当前版本为准。

## 运维

| 命令 | 作用 |
|---|---|
| `make logs s=napcat` | 跟日志，省略 `s` 看全部 |
| `make restart s=astrbot` | 重启单个服务 |
| `make stats` | 内存 / CPU 快照 |
| `make groups` | 列出 bot 所在的群 |
| `scripts/onebot.py <action> [json]` | 直接调 OneBot action，如 `get_group_member_list` |
| `make update` | 拉最新镜像并重建（NapCat 固定版本，不跟着升） |
| `make watchdog-install` / `make watchdog-uninstall` | 装 / 卸掉线看门狗，见下文 |
| `make grok-bridge-install` / `make grok-bridge-uninstall` | 装 / 卸搜烧鸡用的 grok 桥，见下文 |
| `make gh-bridge-install` / `make gh-bridge-uninstall` | 装 / 卸提 issue 用的 gh 桥，见下文 |
| `make claude-bridge-install` / `make claude-bridge-uninstall` | 装 / 卸清 issue 用的 Claude 桥，见下文 |
| `make backup` | 停机打包 `data/`，打包完自动拉起 |
| `make check-public` | 查要提交的文件里有没有不能公开的东西，见下文「提交」 |
| `make bump-guessless` | 猜题子模块推了新提交后，把新版本记进这个仓库 |

- 两个容器各限 1G 内存。实测空载 AstrBot 约 0.3G、NapCat 未登录约 0.2–0.5G，登录后随好友/群规模增长。
- Colima 由 `brew services` 在登录时拉起，容器 `restart: unless-stopped` 跟着起来；电脑睡眠时机器人离线。
- 设了 `NAPCAT_ACCOUNT` 后重启（`make update` / `make backup` / `make down && make up`）会自动快速登录；登录态失效（长时间离线、改密、风控）时日志里仍会出二维码，`make qr` 重扫。
- `.env` 里的 `NAPCAT_WEBUI_TOKEN` 只在首次启动写入 `data/napcat/config/webui.json`，之后改要改那个文件。
- 不要把 6099 / 6185 暴露到公网；需要远程管理走 SSH 转发或 Tailscale。

## 提交

- 仓库是公开的：群号、QQ 号、群名、群友外号、密钥都不能进来。`scripts/check-public.py` 从 `.env`、`data/astrbot/cmd_config.json`、各插件配置、`groups.yaml` 里把这些值读出来，看要提交的文件里有没有；`make hooks` 装成 pre-commit 钩子，每次提交前自动跑。新加了群、群友外号，记进 `groups.yaml`，检查就会认它。要用 uv（脚本自己声明了 PyYAML 依赖）。
- 写文档、注释、测试用例时，群用「主群」「做题群」，群友用「老王」「阿强」这类代称。
- 猜题插件是子模块，有自己的仓库、issue 和 `CLAUDE.md` / `AGENTS.md`：在 `plugins/astrbot_plugin_guessless` 里照常提交、推它自己的仓库，推完回到这里 `make bump-guessless` 记下新版本。
- 提交信息用英文祈使句。

## 防风控和掉线

NapCat 是非官方客户端，腾讯会不定期踢下线或让登录态失效，只能降低概率，见 NapCatQQ 的 #1728、#2071。目前的做法：

- **固定版本**：`compose.yml` 里 NapCat 用 `v4.18.41`，不用 `latest`。社区普遍反映新版更容易被踢，升级要手动改标签。
- **固定设备**：`hostname` 和 MAC 写死成第一次登录时的值。容器重建默认会换新的，QQ 可能当成换了设备。
- **反检测**：`data/napcat/config/napcat_<QQ号>.json` 里 `bypass` 六项全开，不如实上报运行环境。改了要重启 NapCat。
- **少发**：猜题群都设了宵禁（00:00–07:00），整点排行榜每 3 小时一次（猜题插件的 `group_curfews`、`board_interval_hours`）。
- **出口**：QQ 走家宽直连，没经过 Clash 的代理节点。腾讯看到的 IP 来回变、或者是机房 IP，都容易出事。

掉线有两种：

1. 被踢或登录态失效：日志出二维码。
2. 静默掉线：腾讯在服务端断了会话，NapCat 不报错，`get_status` 照样 online，只是收不到消息。2026-10-29 16:11 就是这种，将近 3 小时后才发现。

**掉线看门狗**（`scripts/napcat-watchdog.py`，`make watchdog-install` 装成 launchd 任务，每 5 分钟跑一次）：

- 日志里出了二维码：弹 Mac 通知并打开二维码。
- 白天（09:00–01:00）30 分钟一条消息都没收到，或日志报「账号状态变更为离线」：自动 `docker restart napcat`。会话还有效就快速登录回来，失效了下一轮提醒扫码。
- 两次自动重启至少隔 2 小时，因为登录太勤本身招风控；隔得近就只发通知。
- 记录在 `data/watchdog/watchdog.log`。`python3 scripts/napcat-watchdog.py --dry-run` 只打印判断，不动手。
- 通知是 `osascript` 发的，收不到的话去「系统设置 → 通知」打开「脚本编辑器」。

## 搜烧鸡的 grok 桥

插件 `astrbot_plugin_xsearch`（用法见它的 README）要调宿主机上的 `grok` 命令行，容器里调不到，中间隔一个 HTTP 服务 `scripts/grok-bridge.py`，`make grok-bridge-install` 装成 launchd 常驻。

- 监听 `127.0.0.1:6190`。容器访问宿主机用 `192.168.5.2`（Colima 把它转到宿主机的 127.0.0.1）；`host.docker.internal` 会被 Clash 的假 IP DNS 解析成 198.18.x.x，连不上。
- 请求带插件数据目录里的 `bridge_token`，插件第一次启动时生成。
- grok 只留网页搜索、网页抓取和 X 搜索。不能用 `--tools` 白名单：X 搜索在里面没有名字，名单里有一个不认识的名字，grok 就会把所有工具（包括跑命令）都放开。
- 插件先在容器里用 Google Lens（SerpApi）和 Yandex 搜，社交平台的结果当线索发过来写进提示词。grok 用 `--effort low`，180 秒超时。
- 记录在 `data/grok-bridge/bridge.log`，含 grok 搜过的词。grok 登录失效会弹 Mac 通知，终端跑 `grok login` 就行，桥不用重启。改了 `grok-bridge.py` 要 `launchctl kickstart -k gui/$(id -u)/local.astrbot-napcat.grok-bridge`。

## 提 issue 的 gh 桥

插件 `astrbot_plugin_issue`（用法见它的 README）：开了它的群里有人 @bot 说「提issue …」，DeepSeek 整理好后要在猜题仓库 `YukiagoTpf/astrbot_plugin_guessless` 建 issue。gh 装在宿主机上，登录态在钥匙串里，容器里调不到，也不该把 GitHub token 给容器，所以和 grok 一样隔一层 HTTP 服务 `scripts/gh-bridge.py`，`make gh-bridge-install` 装成 launchd 常驻。

- 监听 `127.0.0.1:6191`，容器经 `192.168.5.2` 访问。请求带插件数据目录里的 `bridge_token`。
- 只能对写死的仓库列 open 的 issue（查重用）、建 issue，标签只能是 `bug` / `enhancement`。别的仓库、关 issue、评论都做不了。
- 每小时最多建 10 个，插件出了毛病也不会往公开仓库里刷屏。
- 记录在 `data/gh-bridge/bridge.log`。`python3 scripts/gh-bridge.py --list` 打印 open 的 issue，可以用来看 gh 登录还在不在。gh 登录失效时群里回「没提成」，日志里是 gh 的报错，终端跑 `gh auth login` 就行。改了 `gh-bridge.py` 要 `launchctl kickstart -k gui/$(id -u)/local.astrbot-napcat.gh-bridge`。

## 清 issue 的 Claude 桥

bot 管理员（`admins_id`，不是群管理员）在任何一个群里 @bot 说「清issue」（「清理一下 issues」「处理issue」也行），插件 `astrbot_plugin_issue` 调宿主机的 `scripts/claude-bridge.py`（`make claude-bridge-install` 装成 launchd 常驻）。它用 herdr 在 `astrbot-napcat` 工作区开一个不抢焦点的新标签页「清issue」，起 `claude --dangerously-skip-permissions`，把猜题仓库 open 的 issue 列表和做法交给它：

- 能弄的（bug、调优、规则说清楚了的新功能）直接改完、推 main、空闲时重启上线，在 GitHub 上评论后关掉。
- 弄不了的（含糊、要管理员拍板、碰红线）不动，最后用 `scripts/onebot.py` 在发指令的群里 @管理员逐个说明，请他上 Mac mini 看；都弄完了就发一句清了哪几个，不 @。
- 给 Claude 的话写死在 `claude-bridge.py` 的 `PROMPT`，插件只传群号和管理员 QQ 号（纯数字），所以令牌泄露了也只能触发这一件事。令牌和 gh 桥是同一个 `bridge_token`。
- 监听 `127.0.0.1:6192`。上一个清 issue 的 Claude（herdr 里名字以 `issues-` 开头）还在 working / blocked 就回「上一轮还在清」；没有 open 的 issue 不起，回「现在没有 open 的 issue」。
- 贴多行 prompt 后紧跟的回车有时被 Claude Code 吞掉，桥会在几秒内看它没开始干活就补按回车。
- 记录在 `data/claude-bridge/bridge.log`。`python3 scripts/claude-bridge.py --test` 起一个只回一句话的 Claude 再关掉，用来确认 herdr 和 claude 能起来。干完的标签页不会自己关，看完手动关。改了 `claude-bridge.py` 要 `launchctl kickstart -k gui/$(id -u)/local.astrbot-napcat.claude-bridge`。
