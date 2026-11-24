# 网哨 NetMon 桌面版使用说明

桌面版把命令行工具包装成一个标准的 macOS 应用（`.app`）：双击打开、图形界面操作、点按钮即可启停监控与生成报告。底层仍是同一套零依赖的 `netmon` 引擎。

## 安装

1. 打开 `dist/NetMon-1.2.1.dmg`。
2. 把 **网哨 NetMon** 拖进 **Applications** 文件夹（DMG 里已附软链）。
3. 从启动台或 Applications 双击 **网哨 NetMon** 打开。

首次打开可能会看到 macOS 的「来自 unidentified developer」提示：右键 → **打开** → 再点 **打开** 即可（本项目未做 Apple 开发者签名）。

## 界面与操作

**语言**：界面与报告默认中文。把数据目录下 `config.json` 的 `"language"` 改成 `"en"`，重启应用后界面、自检详情与生成的 HTML 报告全部切换为英文；改回 `"zh"` 恢复中文。判定逻辑与报告口径不受影响。环境变量 `NETMON_LANG` 可临时覆盖（优先级更高）。

打开后是一个浅色控制台窗口，分四个区域：

- **顶部状态**：右上角胶囊标签显示「监控已停止 / 监控运行中 / 监控异常」（灰 / 绿 / 红三态）。
- **运行信息**：进程 PID、采样间隔、数据目录（过长中间省略，鼠标悬停看全文，旁边「打开」按钮直达目录）。
- **数据卡片**：采样点、硬断网可用率（含「目标 ≥ 99%」阈值着色）、异常事件（硬断网 / 质量异常分色），随数据每 5 秒自动刷新。若当前显示历史数据，卡片上方会出现浅橙提示条。采样点卡片只显示采集窗口，**不显示采样覆盖率**——采样连续性是排障指标，放在仪表盘易与「可用率」混淆，完整覆盖率在报告里呈现。
- **六个操作按钮**：
  | 按钮 | 作用 |
  | --- | --- |
  | 启动监控 | 安装 launchd 常驻服务并立即开始采集（默认 30 秒一轮） |
  | 停止监控 | 卸载后台服务、结束采集 |
  | 生成报告 | 只汇总最新采集日的数据，生成离线 HTML 报告并询问是否打开 |
  | 报告文件夹 | 在 Finder 中打开报告目录，可选择任意一天的 HTML 报告 |
  | 立即自检 | 采集一轮并在弹窗中展示各层探针实测数据与归因结论 |
  | 清除数据 | 归档全部采样数据与报告（需先停止监控），移入时间戳备份目录，可随时找回 |
- **最近事件**：固定列对齐的表格（时间 / 类型 / 时长 / 原因），仅类型列按硬断网（红）/ 质量异常（橙）着色，悬停整行高亮；无事件时显示空状态。

### 运行 / 结束

- 点 **启动监控** → 后台服务常驻运行。**关闭窗口不会中断监控**（这正是监控工具该有的行为）。
- 点 **停止监控** → 立即结束。
- 启动/停止的结果**不再弹模态框**，而是在「最近事件」标题右侧显示一行短暂提示（绿色成功 / 橙色警示，约 5 秒自动消失）；右上角状态胶囊与按钮状态也会同步变化。异常或需要你决策的情况（如监控运行中试图清除数据）仍会明确提示。
- 关闭窗口时若监控仍在运行，会询问你是「同时停止并退出」还是「仅关窗口、后台继续」。

### 清除历史数据

点 **清除数据** → 确认后，会把全部采样/取证数据和报告归档到数据目录下的 `history-backup-时间戳/` 文件夹（例如 `history-backup-20261118-1410/`），界面随即回到「暂无采集数据」，下次启动监控从零开始，旧数据不会混入新采样。

- 需先 **停止监控** 才能清除（避免边归档边写入）；运行中点按钮会给出提示。
- 这是**移动 + 备份**，不是删除：想找回时把备份里的 `data/*.jsonl` 和 `reports/*.html` 移回对应目录即可。日志始终保留。

### 生成报告

点 **生成报告**，应用会复用与命令行完全相同的分析逻辑，输出单文件 HTML 报告（内联 SVG 图表、离线可看），并可一键在浏览器中打开。报告只覆盖**最新采集日**，与仪表盘口径一致；今天和明天的数据各自独立，不会被合并成一段虚假的连续区间。报告会区分「时间跨度」与「有效采样」，显示采样覆盖率与未采集空档；覆盖率低于 80% 时结论标注「样本不足，仅供参考」。报告内容包括结论卡片、可用性时间带、异常事件明细与断网现场取证。

**隐私脱敏（默认开启）**：报告默认隐藏本机内网 IP、网关、设备 MAC 地址与主机名（公网探测目标保留，便于排障），可放心转发给 IT。若需在可信范围内保留真实内网信息，把 `config.json` 里的 `privacy.redact_in_report` 改为 `false` 再重新生成报告。脱敏只作用于报告展示，磁盘上的原始采样数据始终完整。

需要跨天汇总时，用命令行 `python3 -m netmon report --days 7`；桌面版「报告文件夹」按钮会在 Finder 中打开目录，可任选某一天的 HTML 报告。

## 数据放在哪里

桌面版把代码与数据分离，数据统一落在：

```
~/Library/Application Support/NetMon/
  config.json     配置（改完需重启监控）
  data/           ticks-*.jsonl / evidence-*.jsonl / netmon.log / launchd.out.log
  reports/        生成的 HTML 报告
  expired/        超过保留期（默认 30 天）被移出的采样数据
```

这样升级或重装 app 不会碰你的历史采集数据。**安装包不附带任何历史数据**：首次打开时数据目录为空，所有数据只来自你本机真实运行——所以报告统计的就是你实际监控到的结果，不会混入无关的历史记录。

> 想让桌面版接手命令行版的现有数据（在 `项目目录/data/`），把那些 `*.jsonl` 复制到上面的 `data/` 目录即可，然后重启应用。生成报告时只会统计最新采集日；跨天汇总请用命令行 `report --days N`。

超过 `retention_days`（默认 30 天）的采样文件会被**移入 `expired/` 而不是删除**，需要时可手工拷回 `data/`。它们不参与仪表盘和报告的统计。点「报告文件夹」时若还没有报告，界面会直接提示，不会打开一个空目录。

## 卸载

主界面底栏右下角有 **卸载 NetMon…** 入口。点开后按勾选项执行：

- **停止并移除后台服务**（必选）：移除 `~/Library/LaunchAgents/com.netmon.desktop.plist`（旧版 `com.netmon.probe.plist` 也会一并清掉）。不移除的话，每次登录都会尝试拉起一个已经不存在的 app。
- **删除采样数据与报告**（可选）：把整个数据目录移入废纸篓，不是直接抹掉，可拖回恢复。
- **删除应用本体**（可选）：应用退出后把 `NetMon.app` 移入废纸篓。

不选后两项就是"只卸服务、保留数据"，以后重新装上还能接着用历史数据。所有操作都有二次确认，且都走废纸篓，不做真删除。

手动卸载（应用已打不开时）：`launchctl bootout gui/$(id -u)/com.netmon.desktop`，再删 plist、app 和数据目录。升级上来的机器可能还有旧标签 `com.netmon.probe`，新版本启动时会自动迁移；手动清理时可以两个都 bootout 一次。

### 应用被移动/改名后

LaunchAgent 里写死了Python 与源码的绝对路径。把 app 从桌面拖到 Applications 之后，新版会在启动时自动检查并修正这些路径；如果之前监控是开着的，会顺带重载服务，采集不中断。自愈只在 .app 包内运行时发生，源码直跑不会改写你已安装的常驻服务。

### 日志

- `netmon.log`：结构化事件日志，超过 5MB 自动轮转（保留 `.log.1`）。
- `launchd.out.log`：后台采集的逐轮文本输出，超过 4MB 由采集进程**就地轮转**，尾部 512KB 留在 `.log.1`。必须由进程自己处理——launchd 持有文件 fd，外部改名或删除都不生效。

## 开发者：重新打包

打包全程只用 Python 标准库 + macOS 系统工具（`iconutil` / `hdiutil` / `codesign`），无需 pip 安装任何东西。

```bash
# 1. 生成图标（icns + 窗口 png）
python3 packaging/make_icon.py

# 2. 生成 .app（内嵌 Python + Tcl/Tk，约 42MB；默认做 ad-hoc 签名）
python3 packaging/build_app.py
#    --no-embed        不内嵌运行时（约 300KB，依赖宿主机 python3）
#    --python-home P   指定用于内嵌的运行时根目录
#    --keep-pip        内嵌运行时保留 pip

# 3. 一键组装分发包：签名校验 → DMG（含校验）→ 与安装说明打成 zip
python3 packaging/make_release.py
#   产物：dist/NetMon-1.2.1.dmg
#         dist/NetMon-1.2.1-release.zip（DMG + 给同事的安装说明）
```

> 关于签名：`build_app.py` 默认对 app 做 **ad-hoc 签名**（`codesign -s -`），消除"未签名"告警。但它没有 Developer ID、无法公证，同事首次打开仍需「右键 → 打开」；要彻底去掉这一步需购买 Apple 开发者账号做正式签名 + 公证。给同事转发的说明见 `docs/安装说明-给同事.md`。

> 覆盖安装需先 `rm -rf /Applications/NetMon.app` 再复制，否则旧残留文件会让签名失效（"sealed resource is missing"）。

开发时也可直接跑图形界面（不打包）：

```bash
python3 -m netmon gui      # 或 ./netmon.sh gui
```

## 运行环境要求

**应用自带 Python 与 Tcl/Tk，接收方无需安装任何东西。**

`packaging/embed_python.py` 会把一个完整的 Python 运行时（含 Tcl/Tk）收集进
`Contents/Resources/python`，构建时完成三件事：

- **裁剪**：只删运行时绝不需要的部分（`include/`、`test/`、`idlelib`、`pip`、
  `ensurepip` 等），标准库与 `lib-dynload` 全部保留，57MB → 42MB；
- **重定位校验**：确认所有 Mach-O 的非系统依赖都是 `@rpath`，且能在包内 `lib/`
  找到目标。不满足就**报错终止构建**，绝不产出"本机能跑、别人机器上崩溃"的包；
- **自检**：用收集后的解释器在隔离环境（`-I`）里验证 `sys.base_prefix` 指向包内、
  关键标准库可导入、Tk ≥ 8.6。

启动器的选择顺序是**内嵌运行时优先**，系统 Python 只在 `--no-embed` 构建时作为
回退（那套按 Tk 版本打分的逻辑仍在）。后台采集的 LaunchAgent 也复用内嵌解释器
（`netmon/launchd.py::_embedded_python`），不会出现"界面正常但采集回落到系统
Python 3.9 白屏"的错配。

### 架构与系统版本限制

当前收集自 `~/.workbuddy/binaries/python/versions/3.13.12` 的运行时是
**纯 arm64、最低 macOS 11.0**，因此：

- 只支持 Apple 芯片（M1 及以上）的 Mac；
- `Info.plist` 的 `LSMinimumSystemVersion` 由构建脚本按运行时的实际 `minos`
  自动写入（当前 `11.0`），不写死，换运行时会跟着变。

要给 Intel Mac 打包，需提供 universal2 的 Python 3（python.org 官方安装包即是
universal2），用 `--python-home` 指定即可：

```bash
python3 packaging/build_app.py \
  --python-home /Library/Frameworks/Python.framework/Versions/3.13
```

构建结尾会打印检测到的架构与最低系统版本。

### 启动排查

日志：`~/Library/Application Support/NetMon/gui-launch.log`。开头几行是关键：

```
PY_SOURCE=embedded|system     ← 实际用了内嵌还是系统解释器
PY=/Applications/NetMon.app/Contents/Resources/python/bin/python3.13
```

`PY_SOURCE=system` 说明内嵌运行时没被找到，通常是 `--no-embed` 构建，或
`Contents/Resources/python` 被删掉了。

## 常见问题

- **双击后窗口白屏**：自包含构建不应出现。若是 `--no-embed` 构建，先看 `gui-launch.log` 里的 `PY_SOURCE` 与 `PY` 两行。
- **双击打不开 / 提示已损坏**：右键 → 打开。若仍被 Gatekeeper 拦截，终端执行 `xattr -cr /Applications/NetMon.app`。
- **提示「未找到可用的 Python 运行时」**：只可能出现在 `--no-embed` 构建——机器上没有 Tk ≥ 8.6 的 Python 3。装 python.org 官方版，或改用默认的自包含构建。
- **Intel 芯片 Mac 上打不开**：内嵌运行时是 arm64。用 `--python-home` 指向 universal2 的 Python 重新构建。
- **启动监控失败**：检查 `~/Library/Application Support/NetMon/data/launchd.out.log`；多为 launchd plist 权限问题，重试一次通常可解决。
- **可用率数字和命令行看到的不一样**：界面用「硬断网可用率」（只统计断链/断网/DNS 故障），命令行 `status` 用「事件时间跨度」口径，报告里两者都会给出，含义不同但都正确。
