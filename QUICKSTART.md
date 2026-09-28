# 怎么把这个项目跑起来

**不熟悉命令行？直接跳到「方式一：图形操作台」**，全程鼠标点选，不用记任何参数。

---

## 方式一：图形操作台（推荐）

```bash
cd "D:\个人\project\automatedEditingAgent"
python start.py --ui
```

浏览器会自动打开操作台页面（地址 `http://127.0.0.1:8765`）。页面上你只需要：

1. **选素材来源** —— 电脑上的文件夹（推荐）/ 剪映网页端云空间 / 演示数据
2. **读取项目列表** —— 点一下，本地目录或网页端云端的项目会列出来
3. **勾选要剪的项目** —— 想剪哪个勾哪个，可以全选
4. **点「开始生成草稿」** —— 进度条和日志实时显示，跑完在页面里直接看分镜预览

素材怎么从手机进电脑，见下一节「素材从手机进电脑（推荐路线）」。

改端口：`python start.py --ui --ui-port 9000`

也可以直接用 CLI 命令起：

```bash
python run.py serve                # 默认 8765
python run.py serve --port 9000    # 换端口
python run.py serve --no-browser   # 不自动开浏览器
```

> 操作台只监听 `127.0.0.1`（本机），不对外网开放，也不会把你的素材传到任何地方。

---

## 素材从手机进电脑（推荐路线：剪映专业版云空间）

> 背景（实测）：剪映没有公开的云盘 API，网页版（包括本操作台）**看不到手机 App 云空间里的原始素材**（`jianying.com/space` 返回 404）。
> 所以「素材从手机到电脑」这一步，最省事的官方通道是：**电脑版剪映左侧「我的云空间」**。不用数据线、不用第三方网盘，素材不经过任何外部服务器以外的环节。

### 第 1 步：手机上传（拍完视频后）

1. 手机剪映 App → 「我的」→「云空间」→ 上传当月素材，按 `2026-09/项目名` 的文件夹摆放
2. **上传时务必勾选「同步视频素材」** —— 不勾的话电脑端只能看到时间线，看不到原始视频

### 第 2 步：电脑下载（已装剪映专业版 11.3，直接可用）

1. 打开剪映专业版 → 左侧「我的云空间」→ 找到当月文件夹
2. 勾选要的项目 →「下载到本地」，保存位置直接选操作台的素材库：
   `D:\个人\project\automatedEditingAgent\workspace\materials`
   （操作台「电脑上的文件夹」里点「打开素材库目录」按钮，就是打开这个位置）

### 第 3 步：生成草稿

1. 回到操作台 → 来源选「电脑上的文件夹」→ 目录选素材库（或任何你下载到的目录）
2. 「读取项目列表」→ 勾选项目 →「开始生成草稿」

### 注意

- **免费云空间约 512MB～5GB**：原始视频很占空间，下载到电脑后建议把云端副本删掉腾空间
- 不想依赖电脑版剪映的备选：手机下载 → 微信/QQ「文件传输助手」发到电脑 → 拖进素材库对应月份/项目文件夹（多一步，但零依赖）
- 素材落地后的一切（读取项目 → 选片 → 文案 → 配乐 → 生成草稿 → 自动写入剪映）都是自动的，不用再管

---

## 自定义音乐与文案

**自定义音乐**：把 mp3 / m4a / wav / flac 丢进 `assets\music\`（操作台「背景音乐 → 打开目录」直达），刷新页面后下拉框里就能选；「自动匹配」也会从这里面挑。想精细控制情绪标签/BPM，可再在 `config\music_library.json` 登记。注意：请使用有商用授权的音乐。

**自定义文案模板**：把模板 json 放进 `config\templates\`（操作台「文案模板 → 打开目录」直达，目录里有示例文件），刷新页面后下拉可选；与内置 3 个模板共存，id 写成 `floor_showcase` 等可覆盖内置。变量 `{community}`（小区）、`{customer}`（客户称呼）、`{craft}`（工艺）会从项目名自动解析填入。

**文案样式（颜色 / 居中 / 描边 / 阴影 / 加粗）**：统一在 `config\brand_kit.json` 里改，保存后重启 autocut.bat 并重新生成草稿生效。

---

## 方式二：一行命令跑完整流程（无界面）

```bash
cd "D:\个人\project\automatedEditingAgent"
python start.py
```

不需要装任何东西、不需要素材、不需要剪映。
它会用内置演示数据跑完整条流水线，并打印每个项目的产出路径。

想跑完自动弹开预览页：

```bash
python start.py --open
```

想用手机或局域网看预览（起个本地服务，页面可点）：

```bash
python start.py --serve --open
```

### 如果 `python` 命令找不到

用绝对路径就行（这台机器上已验证可用）：

```bash
"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" start.py
```

或者双击项目根目录的 `autocut.bat`，它会自动找 Python。

---

## 方式三：用自己的真实素材（命令行）

把素材整理成**两级目录**（月份 / 项目），然后指给程序：

```
D:\我的素材\
├── 2026-09\
│   ├── 阳光花园-张先生-全屋通铺\
│   │   ├── VID_001.mp4
│   │   └── VID_002.mp4
│   └── 星河湾-李女士-实木多层\
│       └── VID_001.mp4
└── 2026-08\
    └── 翠湖天地-王先生-人字拼\
        └── VID_001.mp4
```

```bash
python start.py --source local --path "D:\我的素材" --open
```

只处理某个月：

```bash
python start.py --source local --path "D:\我的素材" --month 2026-09
```

---

## 方式四：从剪映云空间抓（命令行，进阶）

> **重要前提（实测）**：剪映网页版只提供「云端草稿/项目同步」，手机 App「我的 → 云空间」里的原始素材在网页端看不到（`jianying.com/space` 返回 404）。
> **原始素材请走上一节「素材从手机进电脑」的推荐路线**（手机上传云空间 → 电脑版剪映「我的云空间」下载 → 文件夹模式）。
> capcut-web 模式适合素材已进入云端草稿/网页素材库的场景。

```bash
# 1) 首次：扫码登录，登录态会保存下来
python run.py capcut-login

# 2) 录制一次页面结构（用于校准选择器）
python run.py capcut-record

# 3) 之后就能自动同步
python start.py --source capcut-web --open
```

> 这一步需要先装 Playwright：`pip install playwright && playwright install chromium`
> 并且需要把 `config/capcut_selectors.example.json` 按录制结果填成 `config/capcut_selectors.json`。

---

## 常用命令速查

| 我想…… | 命令 |
|---|---|
| 检查环境缺什么 | `python run.py doctor` |
| 看有哪些项目（不执行） | `python run.py list --source mock` |
| 只跑一个项目 | `python run.py run --source mock` |
| 批量跑全部 | `python run.py batch --source mock` |
| 指定文案模板 | 加 `--template 全屋通铺` |
| 指定背景音乐 | 加 `--music <音乐id>` |
| 跑测试 | `python tests/run_all.py` |

`start.py` 和 `run.py` 的区别：`run.py` 是纯命令行工具；`start.py` 多了自动打开预览、起本地服务、自动写汇总报告这些"给人看"的行为。

---

## 跑完的东西在哪

全部产物都在 `workspace/` 下：

```
workspace/
├── batch_report.json          ← 总览：每个项目状态、时长、告警
├── draft_output/              ← 剪映草稿（可直接拷进剪映打开）
│   └── 阳光花园-张先生-全屋通铺-AutoCut/
│       ├── draft_content.json
│       └── draft_meta_info.json
├── projects/                  ← 每个项目的中间产物
│   └── 2026-09-阳光花园-张先生-全屋通铺-mock/
│       ├── preview.html       ← 分镜预览页（浏览器打开看）
│       ├── timeline.json      ← 时间线（镜头/字幕/音乐）
│       ├── selection_report.json  ← 选片明细（谁入选、为什么）
│       ├── copywriting.json   ← 文案
│       └── render/render.ps1  ← ffmpeg 渲染脚本
└── logs/autocut.log           ← 运行日志
```

**最值得先看的是 `preview.html`** —— 一页就能判断这条片子对不对。

---

## 怎么把草稿放进剪映

草稿生成时**自动**写入两个位置之一：

1. 剪映草稿目录（本机装了剪映时）：
   `C:\Users\<你>\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft\`
2. 或工作区：`workspace\draft_output\`

如果是第 2 种（本机没装剪映），把 `draft_output` 里的整个项目文件夹**拷进**剪映的草稿目录，重启剪映就能在"草稿"里看到。

---

## 当前环境的能力边界

跑 `python run.py doctor` 会打印实际能力。这台机器上：

| 能力 | 状态 | 影响 |
|---|---|---|
| Python 3.13 | ✅ | 核心链路可用 |
| FFmpeg | ❌ 未安装 | 画面分析降级为元数据启发式；无缩略图；渲染脚本暂不可执行 |
| LLM 文案润色 | ❌ 未配置 Key | 文案走模板填槽（可用，个性化弱一些） |
| Playwright（含 Chromium） | ✅ 已安装 | 「扫码登录剪映」和云空间同步可用 |
| 剪映专业版 11.3 | ✅ 已安装 | 草稿自动写入剪映目录；左侧「我的云空间」可下载手机上传的素材 |

**结论：核心决策链路现在就能跑，缺的能力只会降低效果质量，不会阻断流程。**

想补齐 FFmpeg（提升画质分析 + 启用渲染）：

```powershell
winget install Gyan.FFmpeg
```

装完后重开命令行，`python run.py doctor` 里 FFmpeg 会变成 ✅，之后自动启用的能力包括：
- 抽帧分析真实画面亮度、清晰度、晃动 → 镜头分类更准
- 分镜预览页显示真实缩略图
- `render/render.ps1` 可直接执行输出 mp4

---

## 遇到问题

1. **报 `ModuleNotFoundError: autocut`** → 用 `python start.py` 或 `python run.py`，别用 `python -m autocut`
   （这两个启动器已帮你设好路径；`-m autocut` 需要先 `pip install -e .`）
2. **中文乱码** → Windows 下先执行 `chcp 65001`
3. **想重跑一遍** → 直接再跑一次即可，产物会覆盖刷新
4. **看日志** → `workspace/logs/autocut.log`
