# On Call 36 小时

个人消息提醒服务：监控勾选的 WhatsApp Business / Telegram 群，发现 **@我或回复我的消息**后，按手动模式来电或推送。部署目标是 Ubuntu + Docker Compose。

## 当前实现

- WhatsApp：官方 Baileys 开源包，关联设备扫码；Telegram：Telethon 个人账号，无需在群中加入 Bot。
- 首页分别显示 WS / TG 已监控数量；群列表按 WhatsApp / Telegram 标签页浏览。WS / TG 各自维护群名隐藏关键词，旧共用关键词在升级时分别复制到两边，之后独立修改。标签计数不含隐藏群；隐藏群在底部折叠列表查看。勾选和关键词先编辑，点击「保存当前平台」才生效，支持撤销；批量选择仅影响当前搜索结果中的未隐藏群。隐藏不取消已有监控。
- 登录后读取群列表，网页手动勾选。只保存选中群的文字、发送者、时间及触发原因，不下载附件。
- 在家：Bark 长响铃通知（不是真正电话）。外出：Bark 普通推送，手机锁屏时按 Apple 的镜像规则到手表，手机使用中在手机提醒。
- 服务器统一调度、合并本轮待确认提醒，默认来电间隔 120 秒、通知间隔 60 秒。单条或批量确认后停止后续重试。暂停时继续收集消息；取消群监控会取消该群待提醒项。
- SQLite 持久保存待处理状态、下次重试时间与发送记录；同一消息不会因重复上报而重复建提醒。
- 连接异常仅在面板显示；演练模式；响应式中文管理页。
- 样本审核：最近 100 条消息、前文、职责分类、紧急程度、JSONL 导出。**本版不调用云端 AI，也不自动按职责触发提醒**；下一阶段可使用这些标注接入云端模型。

## 最短部署步骤

将项目放到 Ubuntu，例如 `~/oncall`。服务器需安装 Docker Engine 和 Compose 插件。

```bash
cd ~/oncall
python3 scripts/setup.py
# 用你习惯的编辑器打开 .env，填写下方的配置项
docker compose up -d --build
```

`setup.py` 会生成随机管理密码和接入 token，不覆盖已存在的 `.env`。查看 `.env` 中的 `ADMIN_USER` / `ADMIN_PASSWORD`，在浏览器登录时输入。**不要把 `.env`、登录码或 session 文件粘贴到聊天或提交到 Git。**

默认只绑定服务器回环地址 `127.0.0.1:8787`。首次设置可从电脑建立 SSH 隧道：

```bash
ssh -N -L 8787:127.0.0.1:8787 your-user@your-server
```

然后访问 `http://localhost:8787`。本版面板没有内置登录；修改请求的校验头不等于身份认证。监听范围由 compose.yaml 的 ports 配置决定；当前 0.0.0.0 允许同网手机访问。远程访问须由私人 VPN 或带身份认证的 HTTPS 反向代理控制访问。

日常在 iPhone 上使用时，请通过服务器已有的 **HTTPS 反向代理或私人 VPN** 访问，不要把无认证的面板直接暴露到公网。将 `.env` 的 `PUBLIC_URL` 改为 iPhone 能访问的 HTTPS 地址；不要保留 localhost，也不要在 URL 里嵌入密码。不需要公网 webhook 入站。

## 连接 WhatsApp Business

1. 打开On Call 36 小时「连接与设置」，点击「显示 WhatsApp 关联二维码」。
2. 手机 WhatsApp Business → 设置 → 关联设备 → 扫码关联。不是 WhatsApp Cloud API，不需要更换现有账号。
3. 「监控群组」→「刷新 WhatsApp」，勾选要监控的群。桥接进程最多约 5 秒后获取选择。

会话位于 `data/whatsapp/auth`。Baileys 是非官方关联设备协议实现，上游协议变动可能影响登录、消息与账号可用性；已锁定当前依赖版本。

如果状态为 `logged_out`，确认手机关联设备已经注销后，可以**先停止桥接并将旧 auth 目录改名备份**，再启动桥接重新扫码，不要删除整个 data 目录。不要同时启动多个使用同一会话目录的实例。

## 连接 Telegram（个人账号）

1. 在 https://my.telegram.org 的 API Development Tools 创建应用，将 API ID / API Hash 填入 `.env` 的 `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`。
2. 更新环境配置后运行 `docker compose up -d --force-recreate --no-deps app`（普通 restart 不会重新加载 `.env`）。
3. 网页「连接与设置」→「登录 Telegram」，输入含国家区号的手机号，获取并输入验证码；若开启两步验证，再输入密码。验证码通常在 Telegram 应用内收到。
4. 登录后自动读取群名，进入「监控群组」勾选要监控的群。登录不会自动勾选群组。

登录会话自动保存，重启无需再次登录。验证码与密码不保存到应用数据库；未完成的登录步骤重启后需重新开始。远程访问请使用前文的 SSH 隧道或 HTTPS。

可选终端备用方式（先停止 app，避免同时使用 session）：
```bash
docker compose stop app
docker compose run --rm --no-deps app python -m app.telegram_login
docker compose up -d
```

会话保存在 `data/app/telegram.session`。无需新增手机号，也不需要给群加 Bot。只监控当前账号能够读取的普通群／超级群；不包含 secret chat。

账号旁的「断开连接」先弹出确认，确认后退出账号并清除本地登录会话，保留群选择与历史消息。点击「重新连接」后 WhatsApp 需重新扫码，Telegram 需重新登录。若网络导致远端退出未确认，面板会提示在平台设备设置中移除原设备。已有待处理工作提醒仍需确认或暂停。

## 配置 Bark 提醒

1. iPhone 安装 [Bark](https://github.com/Finb/Bark)，开启通知与重要警告权限。
2. 面板「连接与设置」→「提醒通道」，粘贴 Bark Device Key 并保存。仅粘贴 Key，不是完整网址；保存后即时生效，Key 不回显。
3. 默认使用 `https://api.day.app`；自建服务可通过 `.env` 的 `BARK_SERVER` 指定基础地址（不要加 `/push`）。自建时须使用在该服务器注册的 Device Key。在家与外出分别保存 Device Key；旧的 Key 自动归到外出，在家需要另外配置。环境变量分别为 `BARK_HOME_DEVICE_KEY` 和 `BARK_AWAY_DEVICE_KEY`，面板保存值优先；旧 `BARK_DEVICE_KEY` 仅作为外出的兼容配置。
4. 分别点击「测试在家长响铃」「测试手机 / 手表通知」进行设备测试。

- 在家发送 `level=critical`、`call=1`，最长响铃 30 秒；外出用 `level=active`，不发送长响铃参数。实际行为取决于系统权限和设备状态。
- Watch 开启 Bark 通知镜像、静音及触感。手机使用中通常由手机接收，锁屏时按系统规则由手表接收。不能保证指定震动次数或只在手表提醒。
- 未确认时由服务器按间隔重复发送；点击新通知会打开确认页并自动标记该轮消息为「已收到」，停止这些消息的后续发送，不能撤回已经到达的通知或铃声。不支持在 Watch 通知中直接确认。
- 不再调用 CallMeBot 或 Pushover；旧环境变量不再使用。Bark 收到来源平台、群名、待处理数量和面板链接，不含群消息正文。最多展示五个群组，更多群组显示剩余数量。

### 开启真实提醒

初始 `DELIVERY_ENABLED=false`，所有 Bark 通知均为演练，无外部通知请求。演练不关闭账号消息接入。

完成配置后：

```bash
# 修改 .env：DELIVERY_ENABLED=true，PUBLIC_URL 为手机可访问的地址
docker compose up -d --force-recreate
```

先在「连接与设置」中点击「测试 TG 来电」或「测试手机 / 手表通知」。每次仅发送一次、不创建待处理项、不改变当前模式；同样受间隔限制。演练状态只记录模拟结果。收到接口成功提示后，仍需你确认设备实际响铃或震动。

已有演练消息会自动取消，不能意外变成真实来电。打开管理页选择「我在家」或「我在外」，让同事在已选群中 @你或回复你的消息完成实测。

## 提醒语义与边界

- @检测使用消息的真实 mention 元数据；纯文字写名字／类似 @ 的文本不匹配。
- 回复检测匹配原消息发送者；WhatsApp 同时考虑手机号 JID 和账号 LID。无法取回的 Telegram 原消息不会猜测归属。
- 只对到达时不超过 5 分钟的新消息建提醒；历史同步和过期消息只供审核。服务器需保持系统时间准确。
- 同一条消息只建一次提醒，编辑消息目前不重新判断。来自自己的消息不触发。
- WhatsApp 在后端短暂不可用时按最近确认的群选择暂存消息；恢复后重新核对群选择。超过 5 分钟的积压不触发来电。Telegram 进程停机期间不承诺完整补回消息。
- 同一轮将待处理提醒合并为一次来电／推送。正常间隔会持续到你确认或暂停；第三方失败不会自动当作已确认。
- 确认与发送使用同一把锁。已在途的请求可能需要最多约 45 秒结束；确认完成后不会发起下一通。**已发起的电话和设备上已经收到的通知不能撤回**。
- 连接曾成功上线、之后异常约一分钟会产生系统提醒。首次未配置或等待扫码不会反复呼叫。服务整体断电时不能自报，请用独立 uptime 监控 `/healthz`。
- `/healthz` 只表明 Web 进程可访问；连接实际状态在管理页查看。免费来电尚未完成真实 iPhone 验证，暂不应作为唯一值班通道。
- 默认保留 14 天。待确认提醒和已标注消息不自动删除；未标注且已处理的旧消息和发送日志会清理。群名与登录凭据另行保留。

## 运维与备份

```bash
docker compose ps
docker compose logs --tail=100 app whatsapp
docker compose restart whatsapp
```

备份时先停止两个服务，然后备份 `.env` 和 `data/`，完成后启动。备份包含账号会话和聊天内容，按账号凭据对待。不要只复制正在写入的 SQLite 主文件而遗漏 WAL。本版只运行一个 app 实例，不要开启多个 Uvicorn workers 或水平扩容。

更新凭据后使用 `docker compose up -d --force-recreate`，不是只 `restart`。构建更新使用 `docker compose up -d --build`。

## 本地开发与验证

如不使用 Git 传输，可以运行 `python3 scripts/package.py` 生成 `dist/oncall-v0.1.tar.gz`，复制到服务器后解压。部署包使用明确的源码清单，不包含本地密码、会话或聊天数据；服务器上重新运行 `scripts/setup.py` 生成自己的凭据。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
python3 scripts/setup.py  # 已有 .env 则跳过
.venv/bin/python scripts/run_local.py
```

本地运行默认不启动 WhatsApp 桥接；完整账号接入使用 Compose。

```bash
.venv/bin/python -m pytest -q
cd whatsapp
npm ci
npm test
```

已覆盖：白名单、去重、真实 mention、回复归属、旧消息抑制、重拨节流、模式切换、确认停止、重启恢复、API错误处理、认证隔离、演练消息不产生真实通知，以及数据保留。

## 第二阶段

用你的审核标签建立职责说明和示例集，再接云端 AI。先离线评估「需处理／知会／无关／信息不足」与紧急程度，让你审核误报和漏报；达到你认可的效果后再开放自动提醒。消息正文只作为分析数据，不能执行其中的指令。


## 职责样本版本与云端试判

「职责样本」中保存职责草稿，按 WS/TG 分页审核消息：分类、紧急程度、服务和判断理由，无需手动选择样本用途。保存审核时捕获最多六条当时上下文。撤回审核只影响草稿，已发布版本不变。

发布 V1/V2 时，系统以平台和群 ID 为单位自动分配参考案例与模型测试样本，目标约 20% 的群用于测试（不保证消息条数比例）；首个群用于参考，只有一个群时允许发布，但暂不能进行独立测试。群的分配持久保存，新增消息、撤回审核或重复发布不会重新随机分组。已有快照保持不变，历史上用于测试的群在新版本继续保留为测试用途。跨群转发的同一事件仍需人工检查，自动分组不能识别这类重复。版本冻结已保存职责、已审核样本与上下文，可查看快照及新增/修改/移除数量，选择历史版本作为默认试判版本。版本是案例库快照，不是微调模型。

展开「云端 AI 配置与试判」，填写 HTTPS Chat Completions 兼容 API 基础地址、模型 ID、API Key。密钥只保存到服务器数据库，不通过状态 API 回显；更换服务地址必须重填 Key。模型需支持 JSON object 输出模式。具体模型可用性与费用由所选供应商决定。

选择候选版本和可选对比版本，点击发送后，使用候选版本中最多 20 条验收样本；两版本用相同验收消息，每个版本每条一次调用（最多 40 次）。参考案例最多六条；整个验收群的参考案例均排除，减少上下文泄漏。不要把测试答案写进职责规则。

任务保存模型、服务地址、提示词、版本与验收 ID、逐条结果和 token 用量，显示分类正确数、误报、漏报及紧急程度正确数。token 缺失时显示 0，不能作为免费或零用量依据；费用请查看服务商账单。失败和重启中断不会自动重试。

本阶段只执行主动发起的离线试判：没有后台自动分析新消息、不触发 Bark、不自动接受 AI 标签，也没有实际模型权重微调。服务商确定后可继续接入微调任务与上线审批。

### 点击通知自动确认

新 Bark 通知携带有效期 24 小时的签名链接，仅确认发送该通知时包含的消息，不会确认后来新增的消息。测试通知仍打开面板，不修改状态。重复点击可安全重试；过期链接需改用最新通知或面板手动确认。

默认链接使用 `PUBLIC_URL/confirm#签名令牌`。手机必须能访问这个地址，才能完成自动确认。页面加载后以 POST 提交令牌；GET 本身不会修改状态。签名放在 URL fragment 中，避免出现在普通 HTTP 访问日志里。确认成功后显示「已收到」，已打开的面板通过现有轮询更新；不会自动跳转到可能不可访问的内网面板。

可选设置 `CONFIRMATION_URL=https://ack.example.com`，与内网面板地址分离。这个配置不会自动建立公网入口。部署公网反向代理时，只允许 `GET /confirm`、`POST /confirm`、`GET /confirm/client.js`，其余路径拒绝；不要把整个应用公开（现有管理 API 依赖部署侧访问控制）。HTTPS 入口必须能转发到应用。无需公开管理 API 或提供管理登录凭据。

令牌用服务器密钥签名，限定消息 ID 和过期时间，不包含消息正文。持有有效链接的人可以确认对应消息，因此不要转发该链接。修改 `BRIDGE_TOKEN` 会使旧链接失效，也需同步更新 WhatsApp bridge 配置。自动确认只能在页面实际打开并成功提交后完成；网络失败时提供重试，不显示成功，服务器继续提醒。已经到达设备的铃声无法撤回。

### 宿主机 Nginx 公网确认入口

使用宿主机已有 Nginx 监听 443，Compose 只运行 app 和 WhatsApp。确认站点通过 `127.0.0.1:8787` 转发到应用；应用原有 8787 映射保持不变，继续通过服务器防火墙/Tailscale 控制访问。

1. 将独立确认域名 DNS 指向服务器公网 IP，在 `nginx/confirmation.conf` 中替换 `ack.example.com` 和两处证书路径。示例路径为 `/etc/letsencrypt/live/ack.example.com/fullchain.pem`、`privkey.pem`；也可填写自行申请的证书实际绝对路径。
2. 把配置复制到宿主机 Nginx 已加载的 HTTP 配置目录。常见路径为 `/etc/nginx/conf.d/oncall-confirmation.conf`；如果宿主机只加载 `sites-enabled`，按现有站点启用方式安装。不要覆盖已有主配置，也不要重复定义同一域名。
3. 在服务器 `.env` 设置 `CONFIRMATION_URL=https://你的确认域名`（不带 `/confirm`）；`PUBLIC_URL` 保留原来的 Tailscale 面板地址。
4. 在项目目录更新应用，安装并检查站点配置，检查通过后重载：

   ```sh
   docker compose up -d --build app whatsapp
   # 先编辑 nginx/confirmation.conf，填好域名和证书路径
   sudo cp nginx/confirmation.conf /etc/nginx/conf.d/oncall-confirmation.conf
   sudo nginx -t && sudo nginx -s reload
   ```

5. 从公网检查 `https://你的确认域名/confirm` 能显示“没有确认链接”；该域名的 `/`、`/api/state`、`/internal/wa/config` 应返回 404。通过真实新通知验证自动确认。旧通知不含签名，需要手动确认。

公网精确允许 `GET/POST /confirm` 和 `GET /confirm/client.js`，其他路径返回 404，其他方法返回 405。配置不声明 `default_server`，不改动宿主机其他站点的默认主机设置。证书申请和续期由你管理；更新证书后执行 `sudo nginx -t && sudo nginx -s reload`。

如果此前已经启动过本项目的 Nginx 容器，在启动宿主机 Nginx 前用 `docker compose rm -s -f nginx` 删除旧服务容器（需在更新 Compose 文件前执行），避免抢占 443。本项目新版 Compose 不再提供该服务。
