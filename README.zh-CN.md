# iPod Theme Studio

这是基于上游项目 [nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme) 持续开发的一个 GUI 方向 fork，目标是把原本偏命令行的 iPod nano 主题制作流程，逐步整理成更适合普通用户使用的桌面工具。

英文说明请见 [README.md](README.md)。

本项目继承上游的 GPL-3.0 许可证，继续遵守 GPL-3.0。

## 这个 fork 新增了什么

- 新增桌面图形界面入口 [theme_studio.py](theme_studio.py)
- 新增核心工作流封装 [theme_studio_core.py](theme_studio_core.py)
- 新增 Windows 启动脚本 [run_theme_studio.bat](run_theme_studio.bat)
- 新增用于打包的 [build_theme_studio_exe.bat](build_theme_studio_exe.bat)
- 新增便携整合包构建脚本 [build_portable_bundle.bat](build_portable_bundle.bat)，可以生成自带 Python 运行时的免安装目录
- 改善了素材预览、Nano 7 素材分组跳转、以及 `_1888` 升格后的容量风险提醒
- 内置大图裁剪 / 缩放流程，替换壁纸时可以直接在 GUI 里完成取景
- 使用更高质量的缩图链路，降低大图缩到小分辨率时的模糊和颗粒感
- 新增手动降色预览，可把 `1888` 素材尝试降到 `0064` 或 `0065`
- 新增本地素材库，支持收藏当前素材、从电脑导入、搜索、备注、删除、复用、以及对收藏素材做 `1888` 降色
- 收藏素材支持自动判断格式，即使文件名里没有 `_0064` / `_1888` 后缀，列表里也会显示推断结果
- 新增“关于与版权”页面，标明上游项目地址、作者来源和 GPL-3.0 信息
- 改善了 Windows 下的 Python 调用逻辑，减少第一步看似成功、但中间文件没有生成的问题
- README 补上了缺失的 `fs` 依赖说明

## 当前 GUI 已支持的功能

目前这个图形界面主要围绕美术素材工作流：

- 选择设备：`nano6`、`nano7-2012`、`nano7-2015`
- 导入官方固件，或导入社区现成的 IPSW
- 解包 `SilverImagesDB` 到 `body`
- 浏览素材、预览素材、按分组快速跳转
- 替换素材时自动检查尺寸
- 若新图过大，可直接在 GUI 内裁剪 / 缩放到目标分辨率
- 若原素材是低色彩格式，而替换图会被升到 `1888`，会弹出是否降回 `0064` / `0065` 的预览窗口
- 可对当前已经是 `1888` 的系统素材手动降色
- 可将当前系统素材保存到收藏库
- 可从电脑导入图片到收藏库
  - 可以直接保存原图
  - 也可以先输入目标宽高，再进入裁剪 / resize 流程
- 收藏库支持按文件名和备注搜索
- 收藏库支持编辑备注、删除、再次用于替换系统素材
- 收藏库支持对 `1888` 素材手动降色
- 重新打包生成新的 IPSW
- 显示 `SilverImagesDB` 体积变化，作为简单的容量风险提醒

## 便携整合包

现在这个 fork 也支持面向普通 Windows 用户的“免安装整合包”分发方式。

- 运行 `build_portable_bundle.bat` 后，会在 `portable_bundle/iPodThemeStudio_Portable` 下生成整合包目录
- 生成的目录里会自带本地 Python 运行时、GUI 程序和所需模板资源
- 用户只需要解压整个文件夹，然后双击 `launch_theme_studio_portable.bat` 即可启动
- 这套 GUI 便携流程不再要求用户额外安装 Rust、Cargo 或 `arm-none-eabi-gcc`

整合包目录本身主要用于网盘或压缩包分发，不直接提交到仓库。

## 当前还没完全做完的部分

这一版 GUI 目前重点还是 artwork 工作流，下面这些方向还可以继续做：

- 字符串 / 语言资源编辑
- 声音资源替换
- 字体替换
- 更完整的素材别名表
- 更成熟的整合包 / 便携版分发方案

## 如何启动 GUI

如果你已经有可用的 Python 环境，并且安装好了依赖，可以直接运行：

```powershell
python theme_studio.py
```

或者直接双击：

```powershell
run_theme_studio.bat
```

## Python 依赖

至少需要这些包：

```powershell
python -m pip install fs pyfatfs fonttools pillow numpy opencv-python-headless
```

## 关于 Windows 环境

这个项目在 Windows 上比 macOS / Linux 更依赖环境是否正确。

建议开始前先确认：

```powershell
where python
```

第一条最好是真正安装的 Python，而不是：

```text
C:\Users\你的用户名\AppData\Local\Microsoft\WindowsApps\python.exe
```

如果命中的是这个 `WindowsApps` 里的占位符，原始命令行流程有时会出现脚本没有真正执行的问题。

## 关于刷机

这个工具的目标是生成修改后的 IPSW。

刷入设备这一步，仍然建议沿用上游项目的方法，通过 iTunes 或 Apple Devices 完成。这里不另外改写刷机步骤。

## 上游项目

- 上游仓库：[nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme)
- 本 fork 主要是在 GUI / Windows 易用性方向继续扩展，不替代上游原始教程

如果你需要原始的完整教程、命令行工作流和更多背景信息，请回到 [README.md](README.md) 查看英文说明。
