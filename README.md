# ebook_manager

一个使用 Python 3.12、PySide6 和 SQLite 构建的本地电子书管理桌面应用。软件只为电子书建立本地索引，不会移动或修改原始文件；双击书目会调用操作系统默认程序打开。

## 当前功能

- 支持 PDF、EPUB、MOBI、TXT、AZW、AZW3 文件的目录扫描。
- PDF 首页、EPUB 封面元数据及 MOBI/AZW 内嵌封面提取到数据库旁的 `covers/` 缓存目录；原书文件不变。
- 封面按源文件路径、大小和修改时间缓存，缺失封面会显示标题首字和分类图标占位图。
- SQLite 本地数据库、扫描去重与增量索引、自动分类和标签。
- 一本书可关联多个分类；书籍卡片右键可打开、编辑、管理分类与标签或删除书库记录。
- 网格卡片真实封面、悬浮阴影和快捷按钮；缩略图通过 Qt 图片缓存复用。
- 设置支持浅色、深色、跟随系统、启动扫描、封面缓存清理和数据库备份/恢复。
- 实时搜索、收藏、阅读状态、详情面板及调用系统默认阅读器。
- 书库采用分页加载；导入新书后自动切回“全部”并定位最新新增书籍，避免筛选和大书库分页造成误判。
- 用户标签库：多标签维护、改名、删除、合并和书籍批量关联；记录 manual / ai / system 来源。
- 四层分类建议：路径与文件名规则、PDF/EPUB 元数据和文本、可选 OpenAI 兼容服务、基于用户修正的相似书反馈。
- 新导入书籍会自动在后台进行本地内容分析，并生成分类、标签和简介；单本书也可从右键菜单重新分析。
- 分类支持树形层级、移动父级；PDF 与 EPUB 文本只在快速/标准/AI分析时按需读取。
- AI Key 存入操作系统凭据库（keyring），不开启 AI 时分析仅在本地完成；文本上传默认关闭，启用后每次发送前确认。

封面读取仅访问书籍文件中可直接读取的图像资源；没有封面、文件损坏或受保护的文件会使用占位图，不会尝试解除 DRM。

## 环境要求

- Windows 10/11 或支持 Qt 的桌面 Linux
- Python 3.12

## 安装与运行

```powershell
cd C:\Readplan
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Linux/macOS 可使用 `python3.12 -m venv .venv`，激活 `.venv/bin/activate` 后安装依赖并运行 `python main.py`。

## 数据库

数据库默认位置为 `C:\Readplan\database\ebook.db`。应用启动时会自动创建数据库和表、索引；分类和标签不会预置，而是在扫描文件时根据路径、文件名、PDF/EPUB 元数据及可提取文本按需生成。数据库文件已加入 `.gitignore`。

可用 **DB Browser for SQLite** 打开该文件查看 `books`、`categories`、`tags`、`book_tags`、`shelves`、`notes` 和 `reading_log` 等表。

`database.db` 提供 `init_db()`、`get_connection()`、`get_db()` 和仅供调试的破坏性 `reset_db()`。业务 CRUD 在 `database.models` 中。

封面缓存位于 `C:\Readplan\database\covers\`，可在设置中清理。清理缓存不影响书库记录或原始电子书文件；重新扫描时会按需提取。

## 智能分析设置

在 **设置 → 智能功能与 AI 服务** 中选择快速、标准或 AI 模式，填写 OpenAI 兼容 API 地址和模型名称。OpenAI 可使用 `https://api.openai.com/v1`；Ollama 可使用支持 OpenAI 兼容接口的 `http://localhost:11434/v1`。密钥不会写入 SQLite 设置表。

- 快速模式：文件名、目录和嵌入元数据，完全本地。
- 标准模式：在快速模式基础上本地提取 PDF 首页文本或 EPUB 前几章文本。
- AI 模式：先取得本地建议，再向用户配置的服务请求语义建议；不启用文本上传时只发送书名、作者、元数据和本地建议。每次 AI 分析均需隐私确认。
- 新书导入后自动使用标准本地分析；PDF/EPUB 提取有限篇幅的文本，不会自动上传任何信息。生成结果可在详情面板或编辑功能中继续修改，用户手动分类/标签调整会写入 `classification_feedback`，供相似书籍本地匹配。

## 数据库新增结构

- `tags.source`：标签来源 `manual`、`ai` 或 `system`；旧标签升级时标记为 `system`。
- `classification_feedback`：记录书籍、建议/确认分类、建议/确认标签、分析来源和时间。
- `categories.parent_id`：沿用现有自关联字段保存分类树；迁移不会移动原文件或删除书目。

## 项目结构

```text
main.py                 应用入口
database/               SQLite schema、迁移和访问层
scanner/                文件扫描与增量索引
parser/                 PDF / EPUB 本地元数据和文本解析
ai/                     OpenAI 兼容客户端、提示词和分类器
tag/                    标签创建、关联、改名、删除和合并服务
ui/                     主窗口、设置、标签管理和分类审核界面
utils/                  文件、封面缓存及凭据存取工具
tests/                  项目自动化检查
```

## 手动验收方法

1. 运行 `python -m pip install -r requirements.txt`，再启动 `python main.py`。首次启动会自动迁移旧数据库，不应删除或重建原数据库。
2. 导入一批此前未索引的 PDF / EPUB，确认扫描后自动生成分类、标签和简介，检查分类树、标签计数和 `classification_feedback` 表。
3. 右键书籍 → 标签管理：新增、移除标签并保存；在侧栏“管理”中改名、删除、合并，确认书籍关联保留或按预期删除。
4. 设置中分别测试快速、标准模式；标准模式用有可提取文本的 PDF 和 EPUB 验证本地摘要/关键词。
5. 未启用 AI 时断网运行并确认本地分析可用。配置可用的 OpenAI 兼容端点后点“测试连接”；AI 模式检查隐私提示、标题/元数据模式和可选文本发送模式。
6. 修改一次分类或标签，再对标题/目录相近的新书运行分析，观察建议是否参考 `classification_feedback`；无相似反馈时按本地规则给出结果。
7. 用 10,000 本以上的模拟或真实书库观察扫描与分析期间 UI 是否响应；扫描/解析/API 调用应在后台线程，SQLite 查询可在 DB Browser 中检查关联表。
8. 设置 → 数据库中使用“清空所有书籍和扫描路径”，确认数据库中的书籍、分类、标签和扫描目录被清空，磁盘上的原始书籍文件仍存在；再次扫描后应重新生成分类和标签。
9. 导入超过 500 本书后使用列表下方的上一页/下一页翻页，确认全部书籍均可浏览；在搜索、分类或标签筛选状态下导入一本新书，确认界面自动回到“全部”并选中新书。
