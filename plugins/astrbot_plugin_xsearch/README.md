# 搜烧鸡

群友回复一张图 @bot 说「搜烧鸡」（或者带着图直接 @bot 说；「搜 烧鸡」「搜一下烧鸡」「搜个烧鸡」也算），bot 去 X 上找这张图的出处：最早发这张图的帖子，或者图里这个人自己的账号。回复的话由 grok 自己写（找到了说是谁、附帖子链接；没找到说看到了什么线索，比如别的平台的水印），引用那条喊搜的消息发出去。

## 怎么搜

1. **搜图引擎**（第一张图，几秒）：Google Lens（经 SerpApi，配了 `serpapi_key` 才用）和 Yandex 一起搜。
   - Google Lens 只要「完全匹配」。找到 X 上的帖子就直接回，不等 grok。
   - Lens 的链接大多是 `google.com/goto` 跳转（实测 62 条里 60 条），还原不了，所以平台看 `source` 字段，X 上的 @账号从标题「NASA (@NASA) on X」里取，给 grok 的线索只带标题、平台和账号。
   - 其余结果只挑社交平台的页面（X、Pixiv、微博、Instagram、小红书、B 站、YouTube……见 `engines.SOCIAL`），最多 12 条当线索。Yandex 对真人照片找到的多是色情站、泄露合集和长得像的别人，这些不给 grok，也不发群。
2. **grok**（几十秒到 3 分钟）：图和线索一起交给本机的 grok，让它用 X 搜索核实。没有水印、文字，线索也对不上的，提示词让它搜五六次就收工。

Yandex 是直接请求网页，不要 key，但可能被验证码挡住，挡住了就只靠 grok。没用 PicImageSearch 库：装它会把容器里的 lxml 从 6.x 降到 5.x。

- 一个群每小时一次（`cooldown_minutes`），从喊搜的时间算，记在插件数据目录的 `last.json`，重启不丢。搜成了才算次数，不管找没找到；出错、图下不下来不算。
- 群主、群管理员和 AstrBot 管理员（`admins_id`）不受这个限制，搜了也不算次数，不占群友那一次。
- grok 一次只搜一张，搜的时候再喊回「上一张还在搜」，管理员也一样。
- 收到就给那条消息贴个 OK 表情。Google Lens 直接命中的几秒出结果，交给 grok 的一般一分钟内，最多 3 分钟。
- grok 写的回复要过 `main.compose` 的检查：链接以外不超过 80 字、不超过 5 行、不带 `BANNED` 里的词、链接都是社交平台的；不过就换成 `render` 的固定格式（「出处：@xx + 链接」或「X 上没搜到」）。Google Lens 直接命中 X 帖子的不经过 grok，也用固定格式。grok 写的「凭什么认定的」（clue）只记日志。
- 优先级比猜题插件高：题目进行中喊「搜烧鸡」不会被当成猜答案。

## 怎么调到 Grok

grok 命令行装在宿主机上（`brew install --cask grok-build`），登录态在 `~/.grok`，AstrBot 容器里调不到。中间隔一层宿主机上的 HTTP 服务 `scripts/grok-bridge.py`，launchd 常驻（`make grok-bridge-install`）：

- 只监听 `127.0.0.1:6190`，容器经 `192.168.5.2` 访问。请求带插件生成的令牌（插件数据目录的 `bridge_token`）。
- 跑 `grok -p` 时只留网页搜索、网页抓取和 X 搜索，命令、读写文件这些工具都拿掉；推理强度 `low`（实测同一张图从 180 秒降到 42 秒，结果一样），180 秒超时。跑完删掉会话，图不留在 `~/.grok`。
- `data/grok-bridge/bridge.log` 记着每次的结果和 grok 搜过的词，超时的也记。
- 用的是 `grok login` 的 grok.com 账号额度。登录失效时桥会弹 Mac 通知，群里回「没搜成」，终端跑 `grok login` 重新登录就好，不用重启。
- 单独试一张图：`python3 scripts/grok-bridge.py --once 图片.jpg`。

## SerpApi

Google Lens 没有官方 API，直接抓网页会被 Google 挡，所以经 SerpApi。注册 <https://serpapi.com/users/sign_up>，key 在 <https://serpapi.com/manage-api-key>，填进插件配置的 `serpapi_key`。免费版每月 250 次，每小时 50 次；每搜一张图扣一次，没结果、出错不扣。图先传到 SerpApi 的 Image API（500 KB 以内，`main.shrink` 会压到这么小）拿 `image_id`，再搜。

## 局限

grok 没有以图搜图，靠的是水印、角落的 @用户名、图里的文字和特征去 X 上搜关键词。有水印的图基本能找到。干净的图（比如挡着脸的对镜自拍）要靠 Google Lens 收录过 X 上的原图，不然常常找不到；这种图的原帖也常在小红书、微博，不一定在 X。
