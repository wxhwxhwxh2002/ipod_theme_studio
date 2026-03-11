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
- `1888` / `0064` / `0065` 格式工作流
- 手动降色预览与策略选择
- 本地素材库
- 批量导入、搜索、备注、删除、复用
- “关于与版权”页面、项目 branding 和图标

## 当前定位

这一版 GUI 主要聚焦在 artwork 工作流上，适合做这些事：

- 替换壁纸、图标、缩略图等美术资源
- 把修改后的内容重新打包回 IPSW
- 用素材库先收藏、整理、复用图片

还没有重点覆盖的方向包括：

- 字符串 / 语言资源编辑
- 声音资源替换
- 更完整的字体工作流

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

刷回设备这一步，仍然建议沿用上游项目的方法，通过 iTunes、Finder 或 Apple Devices 完成；这里不另外改写刷机教程。

## 上游项目

- 上游仓库：[nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme)
- 本 fork 主要是在 GUI、跨平台易用性和 Windows 便携整合包方向继续扩展，不替代上游原始教程

如果你需要原始的完整命令行流程和更多背景信息，请回到 [README.md](README.md) 查看英文说明。
