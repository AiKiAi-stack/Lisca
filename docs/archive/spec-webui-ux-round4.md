# SPEC — webUI 字幕样式实时调整（round4）

状态: 定案（grill 两轮完成，全采纳推荐）→ 已实现（验收 9/9 + e2e 5/5 绿）
来源: 用户 2026-08-09 需求「增加一个控件来实时调整字幕的字体、大小和位置」；走 ask-matt 流程 grill → SPEC → ticket → implement

## 0. 架构事实（实现者必读）

- 前端唯一文件 `tools/webui_static/index.html`（内嵌 `<style>` + `<script>`）；后端 `GET /`
  整体读取返回 `HTMLResponse`，`Cache-Control: no-store`——改样式/JS 后浏览器必拿到新版
- 叠加字幕 `#current`（index.html 行 58-60 容器 / 行 20-24 CSS）：
  `position:absolute` 于 `#playerWrap`（`bottom:12%`）、`font-size: clamp(26px,3.4vw,38px)`、
  `font-weight:600`、继承 body 字体（`"Noto Sans CJK SC","Microsoft YaHei",sans-serif`）、
  白字 + `text-shadow` 描边泛光、`pointer-events:none`；同一元素同时承载错误提示（`.err`）
  与占位符——**样式控件只改样式、不动内容**
- 显示模型：`syncSubtitle()`（行 145-179）250ms 定时器整块替换 `textContent`（快照冻结），
  样式一经改动即时可见，无需额外刷新链路
- 控件均在 `<header>`（行 43-62），绑定风格 `element.onclick = fn` / `addEventListener`
- **无 localStorage 先例**（全仓库 grep 零命中）——本功能新增该机制
- 约束：`#current`/`#playerWrap`/`#history`/`#player`/`#audioEl` 等 id 与
  `currentEl`/`player`/`historyEl`/`audioEl`/`subs` 等全局变量被 `tests/legacy/e2e_webui.py`
  （行 40-43）直接引用——**不可改名**
- `hidden` 属性需显式 `display:none` 覆盖（行 30-31 注释，面板用 `hidden` 时照抄）
- 本机已装中文字体（fc-list :lang=zh）：Noto Sans CJK SC（黑体）/ Noto Serif CJK SC（宋体）/
  Noto Sans Mono CJK SC（等宽）/ Droid Sans Fallback；**无楷体/圆体**（楷体选项走回退链）

## 1. 需求（grill 定案）

### R1 控件形态：折叠面板
- header 新增「🎨 字幕样式」按钮，toggle 一个折叠面板（含字号/位置/字体三控件 + 重置）
- 面板用 `hidden` 属性 + 显式 `display:none` 覆盖（已知坑）；按钮绑定沿用 `onclick`

### R2 字号（大小）
- 滑块 `<input type="range">`，16–64px，步进 1，**默认 30px**；滑块旁实时显示当前值
- **页面加载即固定应用**（覆盖现有 clamp 响应式）——窗口缩放不再自适应（用户接受此取舍）
- 落点：`currentEl.style.fontSize = <n>px`

### R3 位置
- 垂直滑块，0–30%（`bottom` 百分比），步进 1，**默认 12%**；滑块旁实时显示当前值
- 落点：`currentEl.style.bottom = <n>%`

### R4 字体
- 下拉 5 项：系统默认 / 黑体 `"Noto Sans CJK SC"` / 宋体 `"Noto Serif CJK SC"` /
  等宽 `"Noto Sans Mono CJK SC"` / 楷体（回退链，本机未装则落衬线）
- 系统默认 = 不设置 font-family（继承 body）；其余设置 `currentEl.style.fontFamily`

### R5 实时生效
- 三控件均 `input` 事件即时更新 `#current` 样式（拖动/切换立即可见），无防抖

### R6 持久化
- localStorage 保存三个值（JSON 对象，如 `subtitleStyle`），刷新/重开页面恢复
- 面板开合状态不持久化；无值时不写，加载时统一读取并应用（默认 30px/12%/系统默认）
- 重置按钮：恢复 字号30px / 位置12% / 字体系统默认，并同步滑块/下拉显示

### R7 影响范围
- 仅叠加字幕 `#current`；`#history` 历史列表、日志面板等一律不动

## 2. 验收（扩展 `tests/legacy/e2e_webui.py` 或新增回路，单命令可跑）

1. 面板存在：点「🎨」展开，再点收起
2. 调字号滑块 → `#current` 的 `style.fontSize` == 目标 px（实时生效）
3. 调位置滑块 → `#current` 的 `style.bottom` == 目标 %
4. 切字体 → `#current` 的 `style.fontFamily` 匹配对应栈（系统默认 = 空）
5. localStorage：调整后 `subtitleStyle` 键存在且值正确；**刷新页面后样式恢复**
6. 重置按钮 → 三值回默认
7. 回归：现有 e2e overlay 断言（`#current` 文本同步）保持全绿；`#current` id/全局变量未改名

## 3. 范围外（不做）

- 颜色/背景/透明度、阴影样式调整
- `#history` 历史列表样式
- 字体文件上传（FontFace API）
- 后端任何改动（WS/路由/引擎零接触）
