# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本目录是本机常驻 QQ 机器人的部署：NapCat（QQ 协议端）+ AstrBot（插件框架），跑在 Colima 的 docker 里。本目录是公开的 git 仓库 `YukiagoTpf/yuki-qqbot`，线上数据 `data/` 和 `.env` 不进仓库。猜题插件 `plugins/astrbot_plugin_guessless` 是子模块（GitHub `YukiagoTpf/astrbot_plugin_guessless`），有自己的 `CLAUDE.md` / `AGENTS.md`，改它前先读那份。运维细节见 `README.md`。

## 结构

- NapCat 通过反向 WebSocket `ws://astrbot:6199/ws` 连 AstrBot；两个 WebUI（6099 / 6185）只绑 127.0.0.1。
- `plugins/<name>` 挂到容器的 `/AstrBot/local-plugins`，`data/astrbot/plugins/<name>` 是指过去的软链（宿主机上是断链，正常）。插件代码就是线上代码，改完要重启 AstrBot 才生效（`ASTRBOT_RELOAD=0`）。
- `data/astrbot` 同时挂进 NapCat 的同一路径，NapCat 才读得到 AstrBot 发的图片和语音。
- 容器访问宿主机用 `192.168.5.2`，不要用 `host.docker.internal`（被 Clash 的假 IP DNS 劫持）。搜烧鸡插件 `astrbot_plugin_xsearch` 就是这样调宿主机上 launchd 常驻的 `scripts/grok-bridge.py`，再由它调本机的 `grok` 命令行，见 `README.md`「搜烧鸡的 grok 桥」。
- 提issue 插件 `astrbot_plugin_issue` 同样经宿主机 launchd 常驻的 `scripts/gh-bridge.py` 调本机的 `gh`，在猜题仓库建 issue，见 `README.md`「提 issue 的 gh 桥」。bot 管理员 @bot 说「清issue」时，它再经 `scripts/claude-bridge.py`（launchd 常驻）用 herdr 在 `astrbot-napcat` 工作区开个 bypass 模式的 Claude 处理 open 的 issue，见 `README.md`「清 issue 的 Claude 桥」。
- 线上数据：
  - `data/astrbot/cmd_config.json`：AstrBot 主配置，群白名单在 `platform_settings.id_whitelist`，管理员在 `admins_id`。
  - `data/astrbot/config/astrbot_plugin_guessless_config.json`：猜题插件配置，含 Google Maps key 和 IGDB secret。按群的每小时题数在 `group_hourly_limits`（「群号:题数」，0 不限）。
  - `data/astrbot/plugin_data/astrbot_plugin_guessless/`：题库、缓存、`rounds.json`（进行中的题）、`quota.json`、`scores.json`、`paused.json`。
  - `data/astrbot/groups.yaml`：按群的知识库（黑话、群友外号、每天的留档），群知识库插件每天凌晨往里加，杀炫区在用，改了不用重启。模板是仓库根目录的 `groups.example.yaml`。
  - `data/astrbot/plugin_data/astrbot_plugin_groupkb/chat/<群号>/<日期>.jsonl`：落盘的群聊（留 14 天），查「当时群里在说什么」、做测试样本用它，比 NapCat 的历史全。
  - 两个配置文件都是 UTF-8 BOM，改的时候保留 BOM；里面有密钥，只读取需要的字段，不要打印整个文件。

## 常用命令

| 命令 | 作用 |
|---|---|
| `make logs s=napcat` / `make logs s=astrbot` | 跟日志 |
| `make qr` | 拷出并打开 NapCat 登录二维码 |
| `make groups` | 列出 bot 所在的群 |
| `scripts/onebot.py <action> ['{json}']` | 绕过 AstrBot 直接调 OneBot action（`get_status`、`get_group_member_list`、`get_group_msg_history`……） |
| `make link` | 新增或删除插件目录后重建软链（`make up` 会自动跑） |
| `make new-plugin name=astrbot_plugin_xxx` | 生成插件骨架 |

## 重启 AstrBot

不要在有人猜题、搜烧鸡时重启。确认 `rounds.json` 是 `{}`，20 秒内没人 @bot 发「猜…」「听歌…」，4 分钟内没人发起搜烧鸡（插件最多等 grok 240 秒，重启会把正在等的结果丢掉，2026-10-30 出过一次）。检查和重启写在同一条命令里，中间别隔着别的操作：

```bash
[ "$(cat data/astrbot/plugin_data/astrbot_plugin_guessless/rounds.json)" = "{}" ] \
  && ! docker logs --since 20s astrbot 2>&1 | grep -qE '猜|听歌' \
  && ! docker logs --since 4m astrbot 2>&1 | grep -qE '\[xsearch\].*搜 [0-9]+ 张图' \
  && docker compose restart -t 5 astrbot || echo "有人在用，等会再重启"
```

要停机改配置就把 `restart` 换成 `stop`，改完 `start`。

`scripts/restart-when-idle.sh` 是「130 秒内 bot 没发过图」的旧判断，早于 5 分钟的盲猜题型，不如看 `rounds.json` 准。重启后等日志里出现 `astrbot_plugin_guessless (版本号)` 再验证。

## 排障要点

- **群里 @bot 没反应**：先看 NapCat 日志里有没有「接收 <- 群聊」，再看 AstrBot 日志有没有这条。AstrBot 收到了但插件没接，多半是这个群不在 `id_whitelist` 里（白名单阶段直接丢弃，不报日志）。新群要加进白名单再重启。
- **NapCat 掉线**：`get_status` 可能仍报 online（静默掉线，NapCatQQ#2071）。看最近有没有「接收」日志，或者 `get_group_msg_history` 最新一条的时间。确认掉了就 `docker compose restart napcat`；登录态失效时日志会出二维码，用 `make qr` 打开给用户扫。launchd 上装了掉线看门狗（`scripts/napcat-watchdog.py`），会自动做这两件事，记录在 `data/watchdog/watchdog.log`；防风控的设置（固定版本、hostname / MAC、反检测、宵禁）见 `README.md`「防风控和掉线」，不要随手改回去。连着调 `scripts/onebot.py` 会碰到 WebUI 登录限流（`login rate limit`），隔一会儿再试。

## 在容器里测插件

插件依赖 AstrBot 环境，在 `astrbot` 容器里测。把插件拷到 `/tmp/xxx<数字>/`，数据用线上数据目录的副本，不要在线上数据目录里试。插件的 `AGENTS.md`「验证改动」一节有具体做法。

本机的 hook 会拦 `rm -rf`、强推，也会拦 `docker exec astrbot sh -c "rm -f …"`。清理临时目录用：

```bash
case "$T" in /tmp/xxx[0-9]*) docker exec astrbot rm -r "$T" "$T-data" "$T-out";; esac
```

## git

- 两个仓库（本仓库和猜题子模块）都公开：不能有密钥、QQ 号、群号、群名、群友外号；按群的设置只写在线上配置文件和 `groups.yaml` 里。文档、注释、测试用例里群用「主群」「做题群」，群友用「老王」「阿强」这类代称。
- 本仓库提交前 pre-commit 钩子会跑 `scripts/check-public.py`（`make hooks` 装；`make check-public` 手动跑），它从 `.env`、`data/` 里读出真实的号码、群名、外号、密钥来查。查出来就改掉，不要绕过钩子。
- 提交信息用 `git commit --file` 写（英文，祈使句标题）。
- 猜题插件的改动在 `plugins/astrbot_plugin_guessless` 里提交、推它自己的仓库，推完回本仓库 `make bump-guessless` 记下新版本。
- 仓库所有者可以直接推 `main`，推送时出现「Bypassed rule violations」是正常的；其他人要走 PR。
