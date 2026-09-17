# Labelyou

Labelyou 是一个面向 Windows 的轻量级图片预分类工具，适合在正式标注前快速人工筛选大量图像。它采用类似 Labelme 的逐图工作流，可以把图片移动或复制到四个可自定义的类别目录中。

## 功能

- 打开任意图片目录并后台扫描常见图片格式。
- 四个类别的显示名称及目标目录可以分别设置。
- 使用文件名关键词实时筛选待分类图片，支持空格分隔多个关键词。
- 顶部菜单集中显示文件、编辑、分类和设置命令及对应快捷键。
- 支持移动和复制两种处理模式。
- 使用数字键 `1`～`4` 快速分类。
- 使用 `A`、`D` 或方向键切换图片。
- 使用 `Space` 将图片标记为“不分类”。
- 使用界面按钮或 `Delete` 键永久删除当前图片。
- 分别统计已分类、不分类、待分类和总数。
- 使用 `Ctrl+Z` 撤销本次运行中的最后一步。
- 支持滚轮缩放、鼠标拖动和平移复位。
- 自动保存进度，重新启动后不会重复显示已处理图片。
- 目标目录存在同名图片时自动追加编号，不覆盖原文件。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- Tkinter（Windows 官方 Python 通常已经包含）

## 安装

在仓库目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 启动

激活虚拟环境后直接运行：

```powershell
.\.venv\Scripts\Activate.ps1
labelyou
```

也可以双击 `启动Labelyou.bat`。

不安装命令入口时，可以直接运行：

```powershell
.\.venv\Scripts\python.exe .\image_preclassifier.py
```

## 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl+O` | 打开图片目录 |
| `F5` | 重新扫描 |
| `Ctrl+F` | 聚焦文件名搜索框 |
| `Enter` | 跳到第一个搜索结果 |
| `Esc` | 清空搜索 |
| `1`～`4` | 放入对应的四个类别 |
| `A` / `←` | 上一张 |
| `D` / `→` | 下一张 |
| `Space` | 标记为不分类 |
| `Delete` | 确认后永久删除当前图片 |
| `Ctrl+Z` | 撤销最后一步 |
| 鼠标滚轮 | 缩放图片 |
| 鼠标左键拖动 | 平移图片 |
| 双击图片 | 恢复自适应显示 |

## 数据与隐私

Labelyou 完全在本地运行，不上传图片。以下文件只保存在本机，并已通过 `.gitignore` 排除：

- `settings.json`：源目录、目标目录和处理模式。
- `progress.json`：已处理图片的进度。
- `logs/`：每天的操作记录。

分类为“不分类”的图片仍保留在源目录，但会记录为已处理，因此重新扫描后不会再次进入待分类队列。可以在当前程序运行期间使用 `Ctrl+Z` 撤销。

删除操作会先弹出确认框；确认后图片将从磁盘永久删除，不能通过 `Ctrl+Z` 撤销。

## 开发检查

```powershell
python -m py_compile .\image_preclassifier.py
python .\image_preclassifier.py --self-test
python .\image_preclassifier.py --smoke-test
```
