# iPod Theme Studio

[English README](README.md)

这是基于上游 [nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme) 继续扩展的 GUI fork，目标是把 iPod nano 6 / 7 的美术资源替换流程做得更直观、更适合普通用户。

## 这个 fork 新增了什么

- 桌面 GUI 入口：[theme_studio.py](theme_studio.py)
- 核心工作流封装：[theme_studio_core.py](theme_studio_core.py)
- Windows 启动脚本：[run_theme_studio.bat](run_theme_studio.bat)
- macOS 启动脚本：[run_theme_studio.command](run_theme_studio.command)
- Windows 便携整合包构建脚本：[build_portable_bundle.bat](build_portable_bundle.bat)

当前 GUI 已支持：

- 官方固件与社区 IPSW 导入
- 美术素材解包、浏览、预览、替换、重新打包
- Nano 7 素材分组快捷跳转
- 内置裁剪 / resize
- 更高质量的缩图处理
- `0004` / `0008` / `0064` / `0065` / `0565` / `1888` 格式工作流
- 手动改色彩预览与策略选择
- 本地素材库（支持手动改色彩）
- 批量导入、搜索、备注、删除、复用
- 普通 `.ttf` 字体槽位替换、导出与打包写回
- `STHeiti-Medium.ttc` 的 4 个成员槽位替换：`Heiti TC / Heiti SC / Heiti K / Heiti J`
- `Heiti SC` 的实验性自动预处理写入
- “关于与版权”页面、项目 branding 和图标

## 当前定位

这一版 GUI 主要聚焦在 artwork 工作流上，适合做这些事：

- 替换壁纸、图标、缩略图等美术资源
- 把修改后的内容重新打包回 IPSW
- 用素材库先收藏、整理、复用图片

还没有重点覆盖的方向包括：

- 字符串 / 语言资源编辑
- 声音资源替换
- 完整复刻 FontForge 那类手工字体编辑能力

## 字体功能现状

- 普通 `.ttf` 槽位可以直接替换。
- `STHeiti-Medium.ttc` 在 GUI 里会展开成 4 个可编辑成员：`Heiti TC`、`Heiti SC`、`Heiti K`、`Heiti J`。
- 其中 `Heiti SC` 是当前简体中文主槽位。
- 默认推荐走“安全写入”：
  先用 FontForge 等工具把字体手工处理好，再交给 GUI 写入槽位。
- “实验性自动处理”目前只对 `Heiti SC` 开放：
  会自动继承目标槽位身份并做有限字集裁剪，但不保证任意中文 `.ttf` 都能成功刷机。
- GUI 当前主要负责“把字体正确写入对应槽位”，不是完整替代 FontForge 的自动修字工具。

## Windows 便携整合包

这个仓库也支持给 Windows 用户生成“免安装整合包”：

- 运行 `build_portable_bundle.bat`
- 会在 `portable_bundle/iPodThemeStudio_Portable` 生成整合包目录
- 目录里会包含本地 Python 运行时、GUI 程序和所需模板资源
- 用户解压后双击 `launch_theme_studio_portable.bat` 即可启动

这套 Windows 便携流程不再要求用户额外安装 Rust、Cargo 或 `arm-none-eabi-gcc`。

对于 macOS，目前更推荐直接运行源码版 GUI，而不是追求完整免安装 `.app`。

## 如何启动 GUI

如果你已经有可用的 Python 环境，并且装好了依赖，可以直接运行：

```powershell
python theme_studio.py
```

Windows 可以直接双击：

```powershell
run_theme_studio.bat
```

macOS 可以直接双击：

```powershell
run_theme_studio.command
```

## Python 依赖

至少需要这些包：

```powershell
python -m pip install fs pyfatfs fonttools pillow numpy opencv-python-headless
```

## macOS 用户建议

如果你不用 conda，也完全可以直接在 macOS 终端里把 GUI 跑起来，不需要为了这个项目专门去配 conda。

更推荐给普通 mac 用户的路线是：

1. 如果还没有 Homebrew，先安装：

```shell
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. 安装 Python 3.11：

```shell
brew install python@3.11
```

3. 安装 GUI 依赖：

```shell
python3 -m pip install fs pyfatfs fonttools pillow numpy opencv-python-headless
```

4. 启动 GUI：

```shell
python3 theme_studio.py
```

装好 Python 和依赖后，也可以直接双击：

```shell
run_theme_studio.command
```

如果双击提示没有正确的访问权限，可以先执行：

```shell
chmod +x run_theme_studio.command
./run_theme_studio.command
```

如果仓库是下载或拷贝过来的，macOS 仍然拦截它，可以再执行：

```shell
xattr -d com.apple.quarantine run_theme_studio.command
```

## conda 可选方案

如果你本来就在用 conda，或者更习惯隔离环境，可以统一按下面这组命令来做：

```shell
conda create -n ipod_theme python=3.11 -y
conda activate ipod_theme
python -m pip install fs pyfatfs fonttools pillow numpy opencv-python-headless
python theme_studio.py
```

## 关于 Windows 环境

这个项目在 Windows 上比 macOS / Linux 更依赖 Python 环境是否正确。

建议开始前先确认：

```powershell
where python
```

第一条最好是真正安装的 Python，而不是：

```text
C:\Users\你的用户名\AppData\Local\Microsoft\WindowsApps\python.exe
```

如果命中的是这个 `WindowsApps` 里的占位符，命令行链路有时会出现脚本没有真正执行的问题。

## 关于刷机

这个工具的目标是生成修改后的 IPSW。

真正刷回设备时，建议先备份你 iPod 里的重要内容，再进行下面的操作。

### Windows：通过 iTunes 刷入

按本项目当前测试经验，Windows 上直接使用最新版 iTunes 也可以完成这一步，不需要专门找旧版本。

1. 先在电脑上安装并打开 iTunes。
2. 用数据线连接 iPod nano，等 iTunes 识别到设备。
3. 在 iTunes 窗口左上区域点击小 iPod 图标，进入设备摘要 / 设置页面。
4. 找到“检查更新”按钮。
5. 按住 `Shift` 键，不要松开，然后点击“检查更新”。
6. 这时会弹出文件选择窗口，选择你刚刚在本工具里打包好的 `.ipsw` 文件。
7. 确认更新，等待 iTunes 完成刷入。

这一步建议使用“检查更新”而不是“恢复 iPod”。“检查更新”会按照上游项目的工作流去加载你选定的 IPSW；只有在设备已经无法正常启动，或者进入恢复模式时，才优先考虑“恢复 iPod”。

### macOS：通过访达刷入

1. 用数据线连接 iPod nano。
2. 打开访达，在左侧边栏找到并点击你的 iPod。
3. 进入设备信息页面后，找到“检查更新”按钮。
4. 按住 `Option` 键，不要松开，然后点击“检查更新”。
5. 在弹出的文件选择窗口里，选中你打包好的 `.ipsw` 文件。
6. 确认更新，等待访达完成刷入。

macOS 这里和 Windows 的主要区别只有两点：

- 入口在访达，不是在 iTunes
- 组合键是 `Option + 检查更新`，不是 `Shift`

### 额外说明

- 如果只是正常刷入你打包好的自定义固件，优先使用“检查更新”这条路径。
- 如果设备已经卡在恢复界面、无法正常进入系统，才再考虑“恢复 iPod”。
- 刷入完成后，如果你改的是美术素材，重启并进入系统后就能看到新的壁纸、图标或其他资源。

## 上游项目

- 上游仓库：[nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme)
- 本 fork 主要是在 GUI、跨平台易用性和 Windows 便携整合包方向继续扩展，不替代上游原始教程

如果你需要原始的完整命令行流程和更多背景信息，请回到 [README.md](README.md) 查看英文说明。
