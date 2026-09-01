# Lilies in the box：Windows 桌面引擎调研报告

调研日期：2026-08-23  
适用目标：Windows 11 单显示器、高 DPI；需要与本地 0.5B 模型并行运行；未来保留 Android 客户端能力。

## 先给结论

市面上没有一个现成引擎同时做好动态桌面、桌面外壳、图标编排、桌宠、本地 Agent 和 Android。对 Lilies 最合适的做法不是整体换掉现有程序，而是把不同能力拆开选择。

我建议优先选择 **路线 A：Lilies 自己掌管桌宠、盒子、对话、Dock、图标和系统权限，Lively 作为可选的动态桌面渲染后端**。

这样莉莉丝仍然是产品主体，不会变成另一个桌面软件上的挂件；同时可以较快获得成熟的动态壁纸播放、全屏暂停、多显示器和性能规则。没有安装 Lively 时，继续使用 Lilies 自带的 Qt 实时场景或视频渲染，不影响基本功能。

## 四条可选路线

| 路线 | 组成 | 最适合 | 主要代价 | 我的判断 |
|---|---|---|---|---|
| **A. Lilies + 可选 Lively** | Lilies 保留外壳和桌宠，Lively 只负责动态背景 | 想尽快把动态桌面做稳，又不丢失莉莉丝的产品身份 | 需要做独立后端适配；Lively 是 GPL-3.0，适合以外部程序方式连接，不宜直接复制代码 | **首选** |
| **B. 全部继续用 Qt 原生实现** | PySide6、Qt Quick、Qt Multimedia、Win32 | 想完全掌控视觉、安装包和授权 | 动态壁纸性能策略、编辑器、多屏兼容都要自己打磨 | **次选，最稳妥但开发量最大** |
| **C. 以 Seelen UI 重做外壳** | Seelen 负责任务栏、Dock、窗口、桌面组件；Lilies 变成组件/伴侣 | 想最快拥有一套功能很全的现代 Windows 外壳 | 现有 PySide 界面要大改；WebView/Edge 依赖；AGPL-3.0 会影响发布方式 | **只适合愿意重构并接受开源约束时选择** |
| **D. Wallpaper Engine 适配版** | Lilies 仍是主程序，主题另行导出为 Wallpaper Engine 壁纸 | 最重视美术编辑、粒子、社区内容和 Android 壁纸转移 | 用户必须另购并安装第三方软件；不能成为 Lilies 自己的引擎 | **适合作为以后额外导出格式** |

## 候选产品总表

| 产品 | 类型 | 动态背景 | 外壳 / Dock | 扩展能力 | 接入 Lilies 的难度 | 授权与结论 |
|---|---|---:|---:|---:|---:|---|
| **Lively Wallpaper** | 动态桌面引擎 | 强 | 无 | 强 | 低至中 | GPL-3.0；最适合做可选外部渲染后端 |
| **Wallpaper Engine** | 动态桌面与创作工具 | 很强 | 无 | 强 | 中 | 商业闭源；适合主题导出和美术标杆 |
| **Seelen UI** | 完整桌面环境 | 中 | 很强 | 很强 | 高 | AGPL-3.0；功能最全，但会改变整个项目结构 |
| **Cairo Desktop** | Windows 桌面外壳 | 弱 | 强 | 中 | 中 | Apache-2.0；适合参考恢复、窗口和 Shell 设计 |
| **Rainmeter** | 桌面组件/皮肤引擎 | 弱 | 弱 | 很强 | 中 | GPL-2.0；适合参考组件协议，不适合做主外壳 |
| **Winstep Nexus** | Dock / 任务栏替代 | 无 | 很强 | 中 | 高 | 商业闭源；最适合作为 Dock 交互标杆 |
| **Stardock Fences 6** | 桌面图标管理 | 无 | 局部 | 弱 | 高 | 商业闭源；把功能思想重做进 Lilies，不直接依赖 |
| **Stardock DeskScapes 2026** | 动态壁纸管理/生成 | 强 | 无 | 弱 | 高 | 商业闭源，官方建议 8GB 显存；当前机器不优先 |

## 逐项分析

### 1. Lively Wallpaper：最合适的动态桌面后端

Lively 是 Windows 上成熟度较高的开源动态桌面项目，支持视频/GIF、网页、Unity/Godot 应用型壁纸、屏保和多显示器。它能在全屏程序运行时暂停，支持按前台应用、使用电池和远程桌面状态控制播放；视频由硬件加速播放器处理。官方还提供命令控制，可以切换壁纸、暂停播放、控制桌面图标和传递壁纸参数，适合由 Python 主程序调用。[项目与功能说明](https://github.com/rocksdanister/lively)、[命令控制说明](https://github.com/rocksdanister/lively/wiki/Command-Line-Controls)

对 Lilies 的价值：

- 把 2.5D 场景、电影循环、全屏暂停和多屏问题交给专用引擎。
- Lilies 继续负责莉莉丝人物窗口、盒子、对话、Agent、图标和 Dock，不发生产品身份倒置。
- 可以把“初遇”主题做成 Lively 可识别的壁纸包，同时保留内置 Qt 版本作为回退。
- Lively 没有任务栏、图标编排和本地 Agent，所以不能独立完成本项目。

风险：Lively 使用 GPL-3.0。稳妥方案是让用户单独安装，Lilies 仅通过公开控制接口与它通信；如果以后要把它与安装包一起分发、修改其代码或嵌入其组件，需要单独确认许可证方案。

### 2. Wallpaper Engine：美术和生态标杆，不是可嵌入底座

Wallpaper Engine 对视频、网页、场景、粒子、SceneScript、用户参数和多显示器支持成熟，能在游戏或最大化应用运行时暂停，还有数量庞大的 Steam Workshop 内容。它提供 Android 伴侣应用，可把 Windows 壁纸传到 Android 设备，但这只是壁纸链路，不是 Lilies 对话与系统组件的跨平台框架。[官方功能页](https://www.wallpaperengine.io/en)、[场景编辑器文档](https://docs.wallpaperengine.io/en/scene/overview.html)

适合：

- 作为“初遇”粒子、呼吸、裂光、2.5D 分层和性能选项的质量标杆。
- 以后增加一个“导出 Wallpaper Engine 主题包”的发布渠道。

不适合：

- 商业闭源且依赖用户购买，不能随 Lilies 当作自有运行时发布。
- 不负责任务栏、桌宠窗口、图标和 Agent。

### 3. Seelen UI：现成外壳能力最完整，但不是低成本替换

Seelen UI 是 Windows 10/11 的完整桌面环境替代方案，覆盖可定制任务栏、Dock、启动器、窗口平铺、虚拟桌面、桌面组件、系统弹出层和每工作区壁纸。其主题使用 CSS/JSON，组件 SDK 使用 Svelte、TypeScript 和 IPC。[官方项目与完整功能表](https://github.com/eythaann/seelen-ui)、[主题资源结构示例](https://github.com/eythaann/Seelen-UI/blob/master/src/static/themes/default/metadata.yml)

它最吸引人的地方，是很多 Lilies v0.1 规划中的外壳功能已经存在。但是选择它等于改变项目边界：莉莉丝要成为 Seelen 上的组件，现有 Qt Dock、图标层和 Shell 模式会重复或冲突。它还依赖 WebView/Edge，并采用 AGPL-3.0。

建议只在以下条件同时成立时选择：

- 接受把当前 Windows 界面大幅重构为 Seelen 组件和主题。
- 接受相应的开源发布义务并完成许可证审查。
- 产品目标从“莉莉丝的桌面”转为“Seelen 桌面上的 Lilies 体验”。

否则只参考它的组件清单、主题包结构、按组件独立换肤和系统弹出层设计。

### 4. Cairo Desktop：最值得读的宽松授权外壳参考

Cairo 是 C#/WPF 编写的 Windows 桌面环境，强调稳定、性能和生产力，支持 Windows 7 至 Windows 11，采用 Apache-2.0。[官方仓库](https://github.com/cairoshell/cairoshell)

它的视觉语言偏传统，不适合作为莉莉丝最终外观，但宽松许可证和清晰的桌面外壳定位，使它很适合参考：Explorer 共存、任务切换、程序菜单、桌面导航、Shell 启动和恢复逻辑。由于技术栈不同，主要价值是架构和 Windows 行为参考，而不是直接拷贝界面。

### 5. Rainmeter：组件生态的老师，不是桌面引擎

Rainmeter 的皮肤是可移动、可交互、能记住位置的独立桌面模块；它把信息来源称为 Measures，把显示层称为 Meters，并能通过插件、动作和皮肤包扩展。官方文档列举了系统状态、天气、RSS、便笺、启动应用和媒体控制等用途。[官方手册](https://docs.rainmeter.net/manual/)、[官方仓库与 GPL-2.0 授权](https://github.com/rainmeter/rainmeter)

这个“数据来源与表现分离”的设计很适合 Lilies 的组件协议：组件只提供状态和动作，主题决定它在盒子周围、信笺或 Dock 上如何呈现。但 Rainmeter 的 INI 皮肤体系、视觉一致性和权限模型不适合直接承担 Lilies 主界面。

建议参考，不在 v0.1 绑定；以后可以做一个可选桥接器，让已有 Rainmeter 用户把某些只读数据喂给 Lilies。

### 6. Winstep Nexus：Dock 体验标杆

Nexus 能显示运行应用、分组和筛选任务、承载系统托盘、支持高 DPI/多显示器、抽屉、子 Dock、文件堆栈、拖放打开和大量动画。免费版限个人使用，高级功能为商业产品。[官方功能表](https://www.winstep.net/nexus.asp)

最值得 Lilies 借鉴的是：

- 固定图标与运行任务合并，但用清楚的状态区分。
- 文件夹抽屉和堆栈，而不是把大量图标铺满底边。
- 拖文件到应用图标即可打开。
- 自动隐藏、避让窗口、高 DPI 和多屏位置记忆。

官方也明确说明 Windows 11 23H2 的 XAML Islands 改动破坏了部分 Win32 托盘图标定制。这证明“完全重造系统托盘”长期维护成本高，Lilies v0.1 保留系统抽屉、不过早克隆全部托盘是正确方向。

### 7. Stardock Fences 6：图标编排的功能标杆

Fences 的强项是把桌面内容放入分组，按文件类型、名称、时间和目标位置自动归类；Folder Portals 可以把任意目录映射到桌面；Peek 可用 Win+Space 把分组临时提到所有窗口上方；Chameleon 会让图标在闲置时淡出。[官方功能页](https://www.stardock.com/products/fences/index)

这些功能应重新实现到 Lilies 的虚拟图标层：

- “箱格”：不移动真实文件的虚拟分组。
- “目录入口”：对应 Folder Portal，只显示目录内容的视图。
- “暂时取出”：快捷键把箱格浮到窗口上方。
- “融入背景”：闲置淡出，鼠标靠近或搜索时恢复。

Fences 是商业闭源软件，适合作为产品需求参考，不适合作为运行依赖。

### 8. DeskScapes 2026：暂不选择

DeskScapes 能播放图片和视频、制作播放列表、使用本地 AI 生成/重绘/放大壁纸，并提供大量滤镜。但它是商业闭源产品，主要解决壁纸管理，不解决桌面外壳；官方当前建议 8GB 显存。[官方页面](https://www.stardock.com/products/deskscapes/)

当前电脑只有 6GB 显存，还需要与本地模型并行运行，因此没有理由优先选择它。其“壁纸库、预览、播放列表、效果强度”的管理体验可以参考。

## 对 Lilies 的推荐组合

### v0.1 推荐边界

| 能力 | 建议归属 |
|---|---|
| 莉莉丝桌宠、呼吸、点击旋出功能 | Lilies / Qt Quick |
| 盒子、对话、本地模型、记忆、权限和审计 | Lilies / Python |
| Dock、任务切换、桌面图标虚拟编排 | Lilies / Qt + Win32 |
| 内置实时 2.5D 与视频回退 | Lilies / Qt Quick + Qt Multimedia |
| 高级动态桌面 | 可选 Lively 外部后端 |
| Wallpaper Engine 主题包 | 以后作为额外导出格式 |
| Rainmeter 数据 | 以后做只读桥接，不成为依赖 |

实现上应新增一个“桌面渲染器选择”，让主题清单不关心具体引擎：

1. `内置实时场景`：零依赖，保证所有人能运行。
2. `内置电影循环`：低交互、稳定、容易控制显存。
3. `Lively 高级模式`：检测到用户已安装后才出现。
4. `静态节能模式`：模型运行、使用电池或显存紧张时自动降级。

不要在 Lilies 安装包中自动安装任何第三方引擎；由用户明确选择后再提示安装或连接。

## 选择建议

- 如果你要 **尽快拥有顺滑动态桌面，同时继续把莉莉丝做成独立产品**：选 **A**。
- 如果你要 **完全掌控、以后可能闭源发布、不接受任何外部依赖**：选 **B**。
- 如果你要 **立刻拥有最完整的现代任务栏、窗口管理和组件体系，并接受重构与 AGPL**：选 **C**。
- 如果你更看重 **壁纸编辑、粒子效果、Steam 社区和 Android 壁纸转移**：选 **D**，但它应是额外格式而不是主程序底座。

我个人的顺序是：**A > B > D > C**。

## 选定后应先做的短验证

正式接入前只做一个小型对照原型，不立即重写项目：

1. 让“初遇”主题在 3840×2400 下连续运行 30 分钟。
2. 同时加载现有 0.5B 模型，记录桌面帧率、显存、模型首字时间。
3. 打开全屏应用、最大化应用、锁屏和远程桌面，检查是否正确暂停与恢复。
4. 强制结束 Lilies 和渲染器，确认 Explorer、原任务栏和静态壁纸都能恢复。
5. 验证莉莉丝窗口的透明、拖动、缩放、点击穿透区域不受壁纸后端影响。
6. 卸载或断开第三方引擎后，内置场景仍能正常启动。

## 授权提醒

- Lively：GPL-3.0。
- Seelen UI：AGPL-3.0。
- Rainmeter：GPL-2.0。
- Cairo：Apache-2.0。
- Wallpaper Engine、Nexus、Fences、DeskScapes：商业闭源。

“外部程序通信”通常比直接复制或链接 GPL/AGPL 代码更容易保持边界，但并不自动解决所有再分发问题。若项目以后公开发布或收费，应在打包第三方程序前再做一次正式许可证确认。

