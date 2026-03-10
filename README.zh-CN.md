# iPod Theme Studio

这是基于上游项目 [nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme) 继续开发的一个 GUI 定位 fork，目标是把原本偏命令行的 iPod nano 主题制作流程，逐步整理成更适合普通用户使用的桌面图形界面工具。

原项目许可证为 GPL-3.0，本 fork 继续遵守 GPL-3.0。

## 这个 fork 新增了什么

- 新增桌面图形界面入口 [theme_studio.py](theme_studio.py)
- 新增核心工作流封装 [theme_studio_core.py](theme_studio_core.py)
- 新增 Windows 启动脚本 [run_theme_studio.bat](run_theme_studio.bat)
- 新增 PyInstaller 打包脚本 [build_theme_studio_exe.bat](build_theme_studio_exe.bat)
- 新增 GUI 里的版权/关于页面，注明原项目地址、作者来源与 GPL-3.0
- 优化 Windows 下的 Python 调用逻辑，减少“第一步看起来成功、但中间文件没生成”的问题
- README 补充了缺失的 Python 依赖 `fs`

## 当前 GUI 已实现的功能

目前这个图形界面主要围绕“美术素材替换”这条主线：

- 选择设备：`nano6`、`nano7-2012`、`nano7-2015`
- 导入官方固件，或导入社区现成的 IPSW
- 自动解包 `SilverImagesDB`
- 浏览素材列表、预览素材
- 替换 PNG 素材，并自动检查尺寸
- 对部分原本是调色板格式的素材，在新图颜色超限时自动改成同 ID 的 `_1888.png`
- 按分组快速跳转到常用素材区域
  - Nano 7 图标
  - Nano 7 壁纸
  - Nano 7 壁纸缩略图
- 打包生成新的 IPSW
- 提示 `SilverImagesDB` 的体积变化，作为简单的容量风险提醒

## 目前还没完全做完的部分

这版 GUI 目前重点是 artwork 工作流，下面这些方向还可以继续做：

- 字符串/语言资源编辑
- 声音资源替换
- 字体替换
- 更完整的素材别名表
- 更成熟的 EXE 分发包

## 如何启动 GUI

如果你已经有可用的 Python 环境，并安装好了依赖，可以直接运行：

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
python -m pip install fs pyfatfs fonttools pillow
```

## 关于 Windows 环境

这个项目在 Windows 上比 macOS / Linux 更依赖环境是否正确。

建议在开始前先确认：

```powershell
where python
```

第一条最好是你真正安装的 Python，而不是：

```text
C:\Users\你的用户名\AppData\Local\Microsoft\WindowsApps\python.exe
```

如果命中的是这个 `WindowsApps` 里的占位符，原始命令行流程有时会出现脚本没有真正执行的问题。

## 关于刷机

这个工具的目标是生成修改后的 IPSW。

刷入设备这一步，仍然建议沿用原项目的方法，通过 iTunes 或 Apple Devices 完成。这里不另外改写刷机步骤。

## 上游项目

- 上游仓库：[@nfzerox/ipod_theme](https://github.com/nfzerox/ipod_theme)
- 本 fork 只是继续做 GUI / Windows 易用性方向的扩展，不替代原项目本身

如果你需要原始的完整教程、命令行流程和更多背景信息，请回到 [README.md](README.md) 查看英文原版说明。
