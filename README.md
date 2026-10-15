<p align="center">
  <img src="docs/images/logo.png" alt="Lumen" width="112" height="112">
</p>

<h1 align="center">Lumen</h1>

<p align="center">
  <strong>专注阅读，安静思考。</strong><br>
  一款免费、开源的 PDF 文献阅读器 —— 读论文、跟引用、做标记、记笔记，一处完成。
</p>

<p align="center">
  <a href="https://github.com/lryoooow/PDF-Reader/releases"><img alt="版本" src="https://img.shields.io/badge/版本-0.0.1-6366F1"></a>
  <img alt="平台" src="https://img.shields.io/badge/平台-Windows-0078D4">
  <a href="LICENSE"><img alt="许可证" src="https://img.shields.io/badge/许可证-AGPL--3.0-7C3AED"></a>
  <img alt="免费" src="https://img.shields.io/badge/价格-免费-10B981">
</p>

<p align="center">
  <img src="docs/images/reading-annotations.png" alt="Lumen 阅读界面" width="860">
</p>

---

## 为什么是 Lumen

读文献最怕的不是难，而是被打断：找不到刚才读到哪、点开参考文献就回不来、划过的重点散落各处、
复制一段话出来全是断行。Lumen 想做的，是让这些小麻烦都消失，把注意力还给内容本身。

- **安静的界面**：暖色纸感的版面、克制的配色与柔和的动效，长时间阅读不累眼。
- **为论文而生**：目录、缩略图、参考文献预览、阅读位置记忆，读长文也不迷路。
- **标记即笔记**：高亮、下划线、批注随手加，最后一键整理成 Markdown 笔记。
- **免费、开源、离线**：不需要账号，不上传文件，你的文献只留在你的电脑上。

## 功能亮点

### 读得顺

打开就回到上次读到的那一页；页面清晰（高分屏同样锐利），缩放、跳页、翻页都停在你想看的位置。
左侧栏随时切换**目录**、**批注**与**缩略图**，长篇论文结构一目了然。

### 跟得上引用

正文里的 `[1]` 一点即开**参考文献预览卡**——不用跳走也能先看清引用的是哪篇；想细看就「跳转到条目」，
看完按 `Alt + ←` 回到原处。图表、章节链接同样适用。

<p align="center">
  <img src="docs/images/citation-preview.png" alt="参考文献预览" width="760">
</p>

### 找得到

全文搜索边输边出结果，每条结果都带上下文，`F3` 在命中之间来回跳转，命中位置会被醒目地标出。

<p align="center">
  <img src="docs/images/search.png" alt="全文搜索" width="760">
</p>

### 记得住

- **五色高亮**、下划线、删除线、波浪线，中文也能精确选到每一个字；
- 手写、便签、文本框、签名、时间戳，PDF 上想怎么标就怎么标；
- 所有批注汇总在批注面板，可以补写笔记、点击回到原文；
- 一键**导出 Markdown 笔记**：按页整理好摘录与想法，直接放进 Obsidian、Notion 或 Typora。

### 复制不再断行

「去换行复制」会自动合并 PDF 排版造成的换行和断词，粘贴到翻译工具或笔记里就是一段完整的话。

### 夜里也舒服

浅色 / 深色界面随心切换；**夜间模式**把页面变暗但保留图表颜色，**护眼模式**给页面换上暖色纸张。

<p align="center">
  <img src="docs/images/dark-night-thumbnails.png" alt="深色界面与夜间模式" width="760">
</p>

### 还能做这些

- **加密 PDF**：输入密码即可阅读、搜索、识别与导出，另存时保留原有加密；
- **OCR 文字识别**：扫描版文献也能把文字识别出来，复制或保存为文本；
- **格式转换**：导出为 Word、Excel、TXT 或图片；
- **页面整理**：删除、旋转、合并 PDF，添加半透明水印，随时撤销。

### AI 助读（可选）

接入任意 OpenAI 兼容的大模型服务——DeepSeek、通义千问、Kimi、智谱、豆包、硅基流动、OpenAI、Gemini，
或本机运行的 Ollama：

- 选中文字右键 **AI 翻译 / AI 解释**；拖框选取**公式或图表**让 AI 讲解；
- 一键**结构化总结**、**TL;DR**、**推荐问题**，**速读高亮**直接在页面上标出最该读的句子，或直接就全文提问，回答里的 **[p.N] 点一下就跳回原文**；
- 满意的回答可**存为批注**，和你的笔记放在一起。

AI 默认关闭，只在你主动发起时才把所选内容发送给**你自己配置**的服务；首次发送前会征得同意，
API Key 保存在 Windows 凭据管理器中，不写入任何文件。

## 下载安装

1. 前往 [Releases](https://github.com/lryoooow/PDF-Reader/releases) 下载最新的 `PDFReader-Setup-<版本>.exe`；
2. 双击运行安装向导，按提示完成安装；
3. 打开 Lumen，或在任意 PDF 上右键 →「打开方式」选择 Lumen。

目前支持 **Windows 10 / 11（64 位）**，无需安装 Python 或其他依赖。

<p align="center">
  <img src="docs/images/welcome.png" alt="Lumen 欢迎页" width="760">
</p>

## 路线图

- [x] 目录、缩略图、参考文献预览与跳转
- [x] 多色高亮、批注笔记、Markdown 导出
- [x] 夜间 / 护眼模式、阅读位置记忆
- [x] OCR 文字识别、格式转换
- [x] AI 助读：划词翻译 / 解释、全文总结与问答、框选公式解释
- [x] 多标签页同时打开多篇文献
- [ ] 全文对照翻译
- [ ] 双页 / 书本视图
- [ ] 文献库：收藏、分类与检索

## 参与贡献

欢迎提交 Issue 反馈问题或建议，也欢迎 Pull Request。想参与开发，可以从 [docs/](docs/README.md) 开始了解项目。

## 许可证

Lumen 基于 [GNU AGPL-3.0](LICENSE) 开源发布，可以免费使用、学习与修改。
