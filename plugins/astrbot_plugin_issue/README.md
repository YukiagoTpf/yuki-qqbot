# 提issue

群友 @bot 说「提issue」（「提」可以不说，只说「issue」也行；「提个 bug」「报bug」「提意见」「提建议」「反馈」也算），后面跟上 bug 或者意见（也可以引用一条消息再说，那条也算进去），bot 让 DeepSeek 整理成 GitHub issue，经宿主机上的 gh 提到猜题插件的仓库 `YukiagoTpf/astrbot_plugin_guessless`，回一句「提了 #8：标题」和链接。

```
@bot 提issue 盲猜中国地点猜济源说不认识这个城市
@bot 提个issue：猜游戏能不能加个跳过
（引用一条消息）@bot 提issue
（引用一条转发的聊天记录）@bot issue 来个这个玩法
```

一定要 @bot。回复别人时 QQ 自动带的是 @ 原作者，不是 @bot，要再 @ 一下 bot。不认没 @bot 的：群里随口说「提issues，有 Agent 定时去查」这种话，以「提issue」开头的也有。

引用的那条自己又引用了别的（典型的是：有人引用一段转发的聊天记录说了句话，另一个人再引用这句 @bot 提issue），再往上读一层，最多两层。转发的聊天记录逐条展开交给 DeepSeek，里面说话的人标成「群友1」「群友2」，不带名字；NapCat 没把转发内容一起带回来的，用 `get_forward_msg` 拉。

## 怎么判

DeepSeek（插件配置的 `provider`，线上是 `deepseek-flash`，开思考）先分三类：

| kind | 什么样的 | bot 怎么做 |
|---|---|---|
| `guess` | 对猜题插件的 bug 或建议 | 建 issue；和 open 的 issue 说的是同一件事就不建，回「已经有了 #N」 |
| `other` | 对杀炫区、搜烧鸡或者 bot 本身（掉线、没反应）的意见 | 不建，回「记下了」，记进 `feedback.jsonl` |
| `reject` | 闲聊、玩梗、骂人、看不出想改什么、涉政涉黄、DeepSeek 内容审核拦了的 | 回「没看出要改啥，没提」 |

查重：每次先经 gh 桥拿 open 的 issue（编号和标题），一起给 DeepSeek。拿不到就不查重，照样提。

提示词在 `main.SYSTEM`，里面写了猜题有哪些玩法（`GUESS`），猜题加了新玩法要跟着改。标题按仓库里已有的格式「题型：要点」；正文 bug 写现象、期望，建议写现在、希望，群友没说清的事实列「待确认」。正文后面附上群友原话，模型整理走样了能对照。

## 公开仓库

仓库是公开的，issue 里不能有个人信息：

- 不写提的人的名字、QQ 号。原话里出现群号、bot 和提的人的 QQ 号的，换成「（号码）」。
- 原话里的 `@` 后面加零宽空格，免得在 GitHub 上 @ 到同名的人。
- 原话里的 @ 消息段（多半是 @bot，或者回复时 QQ 自动加的）不要。图和语音只留「[图片]」「[语音]」，不上传。
- 转发的聊天记录不附进原话，只留「[聊天记录 N 条]」，DeepSeek 写的正文里会转述有用的部分。引用的人、聊天记录里的人的 QQ 号也会换掉。
- 谁提的哪条只记在本地的 `feedback.jsonl`。

## 限制

- 只在配置的群里响应（`groups`）。
- 每人每天 3 次（`daily_limit`，0 不限），零点清零，记在 `quota.json`。DeepSeek 判完了才算次数，不管建没建 issue；出错不算。群主、群管理员和 AstrBot 管理员不限，提了也不算。
- 一次只处理一个，同时有人提回「上一个还在提，别急」。
- gh 桥每小时最多建 10 个 issue，超了回「这小时提太多了」。
- 收到就给那条消息贴个 OK 表情，一般几秒出结果。
- 优先级比猜题插件高：题目进行中说「提issue …」不会被当成猜答案。

## 数据

插件数据目录 `data/astrbot/plugin_data/astrbot_plugin_issue/`：

- `feedback.jsonl`：每次判完的记录（时间、群、QQ 号、原话、引用的消息（`quotes`，从早到晚，`text` 是展开后给 DeepSeek 看的）、kind、DeepSeek 写的标题和正文、理由、建的 issue 编号，没建成的有 `error`）。非猜题的意见在这里翻：`grep '"kind": "other"' feedback.jsonl`。
- `quota.json`：今天每人提了几次。
- `bridge_token`：gh 桥和 Claude 桥认的令牌，插件第一次启动时生成。

## 清issue

bot 管理员（AstrBot 的 `admins_id`，不是群管理员）在任何一个群里 @bot 说「清issue」（「清理一下 issues」「处理issue」「issue 清一下」也行，整句只能是这个），插件经宿主机的 Claude 桥开一个 bypass 模式的 Claude Code，把猜题仓库 open 的 issue 改完上线；弄不了的它自己在这个群里 @管理员说明。插件这边只回一句：「开始清 3 个 issue：#11 #12 #13，弄完在群里说」「现在没有 open 的 issue」「上一轮还在清，别急」「没起来，等会再试」。别人说回「这个只有 bot 管理员能用」。

Claude 桥是 `scripts/claude-bridge.py`，配置项 `claude_bridge_url`，见部署目录的 README「清 issue 的 Claude 桥」。

## gh 桥

gh 装在宿主机上，登录态在钥匙串里，容器里调不到，也不该把 token 给容器。中间隔一层 `scripts/gh-bridge.py`，launchd 常驻（`make gh-bridge-install`），见部署目录的 README「提 issue 的 gh 桥」。
