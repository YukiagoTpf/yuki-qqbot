.DEFAULT_GOAL := help
COMPOSE := docker compose
WATCHDOG := $(HOME)/Library/LaunchAgents/local.astrbot-napcat.watchdog.plist
GROK_BRIDGE := $(HOME)/Library/LaunchAgents/local.astrbot-napcat.grok-bridge.plist
GH_BRIDGE := $(HOME)/Library/LaunchAgents/local.astrbot-napcat.gh-bridge.plist
CLAUDE_BRIDGE := $(HOME)/Library/LaunchAgents/local.astrbot-napcat.claude-bridge.plist

help: ## 列出命令
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-11s\033[0m %s\n",$$1,$$2}'

up: link ## 链接本地插件并启动（改了 .env / compose.yml 也用它）
	$(COMPOSE) up -d

down: ## 停止并删除容器，data/ 保留
	$(COMPOSE) down

restart: ## 重启，s=astrbot 只重启一个
	$(COMPOSE) restart $(s)

logs: ## 跟日志，s=napcat 只看一个
	$(COMPOSE) logs -f --tail=200 $(s)

ps: ## 容器状态
	$(COMPOSE) ps

stats: ## 内存 / CPU 快照
	docker stats --no-stream napcat astrbot

qr: ## 打开 NapCat 扫码登录二维码
	$(COMPOSE) cp napcat:/app/napcat/cache/qrcode.png /tmp/napcat-qrcode.png
	open /tmp/napcat-qrcode.png

groups: ## 列出 bot 所在的群（群号 / 人数 / 群名）
	@scripts/onebot.py get_group_list | python3 -c 'import json,sys; [print("%12s  %4s  %s" % (g["group_id"], g["member_count"], g["group_name"])) for g in json.load(sys.stdin)]'

link: ## 把 plugins/ 下的插件软链进 AstrBot
	@scripts/link-plugins.sh

new-plugin: ## 新建插件，name=astrbot_plugin_xxx
	@scripts/new-plugin.sh $(name)

update: ## 拉最新的 AstrBot 镜像并重建（NapCat 上游已删库，只用本机镜像，不拉）
	$(COMPOSE) pull astrbot
	$(COMPOSE) up -d

backup: ## 停机打包 data/ 到 backups/
	@scripts/backup.sh

check-public: ## 查要提交的文件里有没有群号、QQ 号、群名、外号、密钥（仓库是公开的）
	@scripts/check-public.py

hooks: ## 装 git 的 pre-commit 钩子：每次提交前跑 check-public
	@printf '#!/bin/sh\nexec scripts/check-public.py\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "装好了：.git/hooks/pre-commit"

bump-guessless: ## 猜题子模块推了新提交后，把新版本记进这个仓库
	@git add plugins/astrbot_plugin_guessless
	@if git diff --cached --quiet plugins/astrbot_plugin_guessless; then echo "猜题没有新提交"; else \
		git commit -q -m "Bump guessless to $$(git -C plugins/astrbot_plugin_guessless log -1 --format='%h: %s')" -- plugins/astrbot_plugin_guessless && git log -1 --oneline; fi

watchdog-install: ## 装掉线看门狗：每 5 分钟查一次，掉线自动重启 NapCat，要扫码弹通知
	@mkdir -p data/watchdog
	@sed "s|__ROOT__|$(CURDIR)|g" scripts/napcat-watchdog.plist > $(WATCHDOG)
	@launchctl bootout gui/$$(id -u) $(WATCHDOG) 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(WATCHDOG)

watchdog-uninstall: ## 卸掉掉线看门狗
	-launchctl bootout gui/$$(id -u) $(WATCHDOG)
	rm -f $(WATCHDOG)

grok-bridge-install: ## 装搜烧鸡的 grok 桥：launchd 常驻，让容器里的插件调本机的 grok
	@mkdir -p data/grok-bridge
	@sed "s|__ROOT__|$(CURDIR)|g" scripts/grok-bridge.plist > $(GROK_BRIDGE)
	@launchctl bootout gui/$$(id -u) $(GROK_BRIDGE) 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(GROK_BRIDGE)

grok-bridge-uninstall: ## 卸掉 grok 桥
	-launchctl bootout gui/$$(id -u) $(GROK_BRIDGE)
	rm -f $(GROK_BRIDGE)

gh-bridge-install: ## 装提 issue 的 gh 桥：launchd 常驻，让容器里的插件用本机的 gh 建 issue
	@mkdir -p data/gh-bridge
	@sed "s|__ROOT__|$(CURDIR)|g" scripts/gh-bridge.plist > $(GH_BRIDGE)
	@launchctl bootout gui/$$(id -u) $(GH_BRIDGE) 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(GH_BRIDGE)

gh-bridge-uninstall: ## 卸掉 gh 桥
	-launchctl bootout gui/$$(id -u) $(GH_BRIDGE)
	rm -f $(GH_BRIDGE)

claude-bridge-install: ## 装清 issue 的 Claude 桥：launchd 常驻，管理员说「清issue」时用 herdr 开个 Claude
	@mkdir -p data/claude-bridge
	@sed "s|__ROOT__|$(CURDIR)|g" scripts/claude-bridge.plist > $(CLAUDE_BRIDGE)
	@launchctl bootout gui/$$(id -u) $(CLAUDE_BRIDGE) 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(CLAUDE_BRIDGE)

claude-bridge-uninstall: ## 卸掉 Claude 桥
	-launchctl bootout gui/$$(id -u) $(CLAUDE_BRIDGE)
	rm -f $(CLAUDE_BRIDGE)

.PHONY: help up down restart logs ps stats qr groups link new-plugin update backup check-public hooks bump-guessless watchdog-install watchdog-uninstall grok-bridge-install grok-bridge-uninstall gh-bridge-install gh-bridge-uninstall claude-bridge-install claude-bridge-uninstall
