# DGStudio — DG-Lab × Everything 控制台

**DGStudio** 是基于 **Python + WinUI 3** 的 DG-Lab（郊狼 Coyote / 负鼠 OVC / 灵猫 BMTR）控制台，采用**模块化架构**：软件主体只包含核心（设备连接与参数模型），对外联动（VRChat OSC、Alice in Cradle 等）以**联动模块**形式提供，在「模块」页按需下载、实时装卸、热重载。

```
┌────────────────┐            ┌──────────────┐                      ┌────────────────┐            ┌────────────┐
│ 郊狼/负鼠/灵猫  │◄── 蓝牙 ──►│ DG-Lab App   │◄─ WebSocket V4/V3 ──► │ DGStudio 核心  │◄─ 事件流 ─►│ 联动模块    │
│ 支持多台在线    │            │ (4.x / 3.x)  │ （公网或本机自建）     │ 连接层（入口）  │            └────────────┘
└───────┬────────┘            └──────────────┘                      └──────┬─────────┘
        ▲                     蓝牙直连（不经 App）                          ▲
        └──────────────────────────────────────────────────────────────────┘
```

> 设备数据有两条入口：**中继**（设备 ─蓝牙→ DG-Lab App ─WebSocket V4 / V3→ 核心连接层，走公网中继或本机自建中继均可）与**蓝牙直连**（设备 ─BLE→ 核心连接层，不经 App）。核心与联动模块之间统一经**事件流**双向传入 / 传出，模块再对接外部程序（VRChat OSC 9000/9001、Alice in Cradle HTTP `/data` 等）。

## 功能

### 连接方式

| 连接方式 | 说明 |
|---|---|
| **Socket V4**（推荐） | DG-Lab 4.0 App 的「Socket V4」控制入口，1 控制方 : N 被控方，扫码配对。郊狼 / 负鼠 / 灵猫均可接入，支持多台同时在线 |
| **Socket V3** | 官方旧版中继协议，兼容 3.x App 与自建中继（仅郊狼 3.0） |
| **蓝牙直连** | 郊狼 **3.0**（`47L121000`）、郊狼 **2.0**（`D-LAB ESTIM01`）、负鼠 **OVC**（`47L127000`）、灵猫 **BMTR**（`47L124000`）。**支持多台设备同时连接**（可混合型号），每台在「控制」页各有独立卡片 |
| **本地中继** | 打开后本机即成为局域网中继服务器（V4 默认 9998 / V3 默认 9999），App 扫码直连电脑，不依赖任何外部服务器 |

### 设备控制

「控制」页每台设备一张独立控制卡片，卡片之间互不影响：

* **强度**：加减按钮（步长可设）与「直接设置」（填入 A/B 数值后应用）；通道旁「归零」清强度并切回静默波形，顶部「全部归零」一次清零所有设备。
* **波形**：下拉直接跳变，或用 ‹ / › 逐个切换并循环。默认「静默」，选择任意波形即开始输出。末位的「**外部脉冲流**」不由内置波形发生器生成，而是按联动模块每 0.1s 推入的频率数据逐帧成流（如「音频联动」模块让输出频率跟随声音音高、电平跟随响度）。
* **开火**：「一键开火」按本卡「开火强度」爆发指定秒数后自动恢复；「按住持续开火」按住起爆、放开停止。开火时若通道处于静默会临时切「持续」波形，结束后自动切回原波形。
* **实时波形图**：每通道一排柱状图，显示最近数秒的实际输出强度，最右侧为最新采样。
* **本卡参数**：最大强度上限、开火强度、加减步长**仅对本设备生效**；开火强度为 0 时跟随本卡上限。
* **LED 与外设**：蓝牙直连时郊狼 / 负鼠 / 灵猫均可切换 LED 颜色（熄灭 / 黄色 / 红色 / 紫色 / 蓝色 / 青色 / 绿色）。
* **灵猫（BMTR）**：纯传感器设备，本页不下发任何波形或强度指令，只显示气压读数、边控状态与最近 60 秒气压曲线（0-60 kPa），并提供「气压清零」与「翻转屏幕」。

### 负鼠按键映射（蓝牙直连）

负鼠的 11 个物理按键可逐个绑定动作：

* **通道操作**：A/B 通道强度加 / 减 / 归零，波形上一个 / 下一个
* **持续开火**：按住开火、放开停止
* **急停**
* **发送 OSC 参数**：按下发 1、抬起发 0，地址可自由填写（该动作由 OSC 模块动态提供，模块未安装时不会出现在选项里）
* **模拟键盘按键**：按下 / 抬起同步注入键盘，可用于触发 VRChat 等程序的热键；选中后点输入框按任意键即完成绑定

绑定支持**多套配置文件**，可随时切换、新建（以当前映射为模板）或改名，切换即时生效。

### 联动模块（按需下载 + 热重载）

软件核心**不内置联动模块**，在「模块」页点「获取在线列表」检索、
「下载」取回模块文件、「安装并启动」即用。安装时自动补装模块声明的
依赖（如 `python-osc`、`opencv-python-headless`），无需手动处理——
打包版经随包内置的 Python（`_python\`）安装，目标机器无需安装 Python；
卸载 / 更新即**热重载**生效，无需重启。

当前可用模块：

| 模块 | 仓库 | 版本 | 说明 |
|---|---|---|---|
| **VRChat OSC 联动** | [dgstudio-modules-osc_bridge](https://github.com/KelierAndes/dgstudio-modules-osc_bridge) | 1.11.0 | 头像参数双向映射，用表达式在核心参数与头像参数之间换算 |
| **Alice in Cradle 联动** | [dgstudio-modules-alice_cradle](https://github.com/KelierAndes/dgstudio-modules-alice_cradle) | 0.6.5 | 从游戏模组读取 HP / MP 等数值，按映射表映射到核心参数 |
| **画面识别联动** | [dgstudio-modules-vision_link](https://github.com/KelierAndes/dgstudio-modules-vision_link) | 0.3.2 | OpenCV 检测屏幕画面（模板图像 / 数值条 / 数字）产生数值，按映射表映射到核心参数 |
| **音频联动** | [dgstudio-modules-sound_link](https://github.com/KelierAndes/dgstudio-modules-sound_link) | 0.3.2 | 采集麦克风 / 系统声音输出响度、频率等映射变量，输出频率跟随声音音高（配合「外部脉冲流」波形） |
| **灵猫边控联动** | [dgstudio-modules-margin_control](https://github.com/KelierAndes/dgstudio-modules-margin_control) | 0.11.0 | 灵猫气压 / 官方边控会话驱动的闭环边控：红线 / 蓝线、持续判定、气压跳变与阈值自适应，达限自动释放 |
| **手柄震动联动** | [dgstudio-modules-xinput_oscillate](https://github.com/KelierAndes/dgstudio-modules-xinput_oscillate) | 0.2.1 | 经虚拟手柄接收游戏原生 XInput 震动，按映射表派发到核心参数 |
| **强度日志示例** | [dgstudio-modules-strength_logger](https://github.com/KelierAndes/dgstudio-modules-strength_logger) | 0.1.0 | 演示模块 API：订阅强度变化写入日志（开发模板） |

「初始化配置」为内置核心模块（启动时按各模块声明自动补齐配置文件），随软件分发。各模块的详细说明、安装与配置见各自仓库的 README。

网络受限环境下，可在「设置 → 模块市场」配置**市场仓库**（任意 fork 镜像）、
**GitHub 加速前缀**（内置 ghfast.top / gh-proxy.com / ghproxy.net 常用源，
可自行输入）与**网络代理**（内置 Clash 7890 / Clash Verge 7897 / v2rayN 10809
常用端口，留空跟随系统）；配置即时生效，回模块页重新获取列表即可。

### 界面

左侧导航分为 **概览 / 连接 / 控制 / 联动 / 模块 / 日志 / 设置**，底部为深色 / 浅色主题切换。

* **概览**：设备统计卡（已连接设备、输出设备、已启用输入 / 输出链路数）、输入 / 输出通道卡（含通道探活与实时数据值），以及每个联动模块的双向通道行（映射条数 + 启用 / 探活状态）。探活以对端实际通信为准，游戏未启动时如实显示等待状态。
* **连接**：Socket V4 / V3 连接卡片（连接、断开、配对二维码，生成后自动展开）、蓝牙扫描与设备连接，以及已保存设备的一键重连 / 删除记录。
* **联动**：按模块分类的大卡片，每模块一张**双向映射表**（输出映射 / 输入映射 / 模块设置），映射改动保存后热生效。
* **日志**：按等级筛选（全部 / 调试 / 信息 / 警告 / 错误）、关键词搜索、清空记录。
* **模块**：在线模块的检索 / 下载 / 更新，本地模块的实时装卸与依赖管理。
* **设置**：V4 / V3 中继服务器地址与本地中继开关、端口；自动重连；深色主题；记录通信数据帧与写日志文件开关；配置文件的载入 / 保存；以及版本与运行时信息。

## 安装与运行

### 方式一：下载发布包（推荐）

到 [GitHub Releases](https://github.com/KelierAndes/DG-Lab-Studio/releases) 下载 `DGStudio_v*_win64.zip`，**解压到任意位置**即可运行：

```
DGStudio\
  DGStudio.exe     主程序
  _internal\       依赖目录（必须与 exe 放在一起）
  _python\         内置 Python 运行时（模块依赖自动安装用，必须与 exe 放在一起）
  modules\         仅内置核心模块（联动模块首次为空，在「模块」页按需下载）
```

发布包为干净软件包：不含配置文件（首次运行自动生成 `config.json` 与 `config/`），也不含联动模块。双击 `DGStudio.exe` 启动。目标机器需安装一次 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads)（未装会弹出官方提示）。

### 方式二：从源码运行

需要 Windows 10 1809+ / Windows 11，Python 3.9+。核心依赖仅 5 项
（蓝牙 / 中继 / 二维码 / 图像 / UI），模块依赖由各模块声明、安装时自动补装：

```bat
git clone https://github.com/KelierAndes/DG-Lab-Studio.git
cd DG-Lab-Studio
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

依赖的精确版本记录在 `requirements.lock.txt`；联动模块源码在
[dgstudio-modules-market](https://github.com/KelierAndes/dgstudio-modules-market) 仓库。

### 设备连接

* **Socket V4 / V3**：先在「设置」页填好「中继服务器地址」（留空使用默认官方中继），回到「连接」页点连接并生成配对二维码，用 DG-Lab App 扫码（4.0 用 Socket V4 入口，3.x 用 Socket 入口）。
* **蓝牙**：进入「蓝牙直连」→「扫描设备」→ 选中设备 → 连接。扫描与连接前请确保设备未被手机 App 占用。
* **本地中继**：在「设置」页打开「使用本地中继」后连接，二维码会直接给出局域网地址，手机与电脑处于同一局域网即可扫码直连。

## 配置文件

| 文件 | 内容 |
|---|---|
| `config.json` | 引擎与设备参数：连接地址、强度上限、开火、蓝牙、中继、界面主题等 |
| `config/<模块>.json` | 各联动模块自己的配置（如 `osc.json` 的映射表、`alice_cradle.json`） |
| `config/modules.json` | 各模块的启用状态 |
| `config/market_cache.json` | 模块市场清单（market.yaml）的缓存，离线时回退显示 |

配置文件由核心模块按各模块声明自动补齐；旧版本的单文件配置会在启动时自动迁移到上述结构。日志写入应用目录的 `dgstudio.log`（可在「设置」页关闭），崩溃日志在 `%TEMP%\dgstudio_crash.log`。

## 常见问题

* **蓝牙扫描不到设备**：设备可能正被手机 App 占用，先在手机 App 中断开。
* **按键映射有反应但游戏没响应**：模拟键盘动作只送到**当前前台窗口**，日志中会记录前台窗口标题，可据此确认焦点是否落在目标程序上。
* **模块安装后启动失败（缺依赖）**：在「模块」页该卡片点「安装依赖」重试；源码运行也可手动 `pip install` 模块声明的依赖（见模块仓库各 README）。
* **模块页拉取不到在线列表 / 下载缓慢**：多为网络原因（GitHub 访问受限）。在「设置 → 模块市场」选择 GitHub 加速前缀或填写本地代理地址后重试；软件会自动回退到上次成功获取的缓存清单；也可手动下载某个模块子仓库（`dgstudio-modules-*`），把其 `modules/<id>/` 文件夹解压到应用目录 `modules/` 后「扫描模块目录」。

OSC / 游戏联动相关的常见问题见模块仓库对应模块的 README。

## 安全提示

这是电刺激设备控制工具。请从低强度开始，**始终为每台设备设置「最大强度上限」**（该设备的强度设定都会被钳制到该值），急停可随时清零全部强度并重置波形。波形参数不当可能引起刺痛。请自行承担使用风险，并遵守官方协议的非商业使用条款。

## 许可

本项目以 **GNU General Public License v3.0**（GPL-3.0）发布，全文见 [LICENSE](LICENSE)。

Copyright (C) 2026 Kelier Andes

本项目包含移植自同样以 GPL-3.0 授权的官方代码（波形数据来自 [dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python)，本地中继移植自 [dglab-websocket-server](https://github.com/dungeonlab-open/dglab-websocket-server)），GPL-3.0 要求衍生作品整体以 GPL-3.0 分发。联动模块携带的 BepInEx 及其组件适用其各自许可。

DG-Lab 官方协议文档另有「协议部分禁止商用」条款，商用前请自行确认并遵守。

联动模块与其开发文档见 [dgstudio-modules-market](https://github.com/KelierAndes/dgstudio-modules-market) 仓库。
