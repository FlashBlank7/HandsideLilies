# 正式姿态素材接入门槛

此门槛只负责审计素材，不生成、不修改、也不自动抠取位图。概念图只有在重新导出为合格的生产素材后，才允许加入 `ThemeManifest.assets`。

## 清单契约

正式主题在 `character` 中声明：

- `poseAssetGateVersion: 3`；
- 每个 `recipe: "pose-artwork"` 的 `poseBundles` 条目必须提供 `artworkAsset`；
- `poseArtworkSpecs` 以相同的 asset key 为键，声明 `resolutionTier`、`pixelSize`、`minimumSize`、`aspectRatio` 和 `quality`；
- `assets` 中所有形如 `poseXxx` 的运行时资源必须一一具备规格，不能存在未使用规格或未审计素材。

v2 起允许多个姿态共享一张生产级 sprite sheet。此时 bundle 还必须声明
`spriteId`，素材规格必须使用 `layout: "2x2-custom-clips"` 并为四个 sprite
分别记录 `poseId`、象限、`sourceRect`、稳定锚点、红绳锚点和点击遮罩。
运行时只通过 QML `Image.sourceClipRect` 读取这些区域，不裁切或重写原图。
门禁以 alpha≥16 的主体边界检查每个自定义 clip：主体必须位于所声明象限、
与四边保留安全距离、点击遮罩必须覆盖主体，同时继续检查透明区 RGB 泄漏、
绿边和白色蒙版风险。机械四等分若会切到相邻姿态，必须拒绝。

v3 将独立人物图的点击轮廓也移入 `poseArtworkSpecs.clickMask`，不再让新图
继承某个旧姿态的硬编码大矩形。独立图支持归一化的 `rect`、`ellipse`，或由
`rects`、`ellipses`、`polygons` 组成的 `composite` 并集；多边形至少三个点，
所有形状必须完整落在 `0..1` 画布内。QML 在镜像人物时反向换算查询坐标，
因此左右屏幕边缘使用同一份源图轮廓也不会错位。缺失、空白或越界遮罩一律
拒绝，只有旧的第三方主题在完全未声明遮罩时保留兼容回退。

可选姿态即使 `optionalArtworkEnabled: false` 也必须同时声明 asset 映射、
`optional: true` 的规格、生产尺寸、锚点、点击轮廓和兼容服装；PNG 尚未完成时
允许以 `dormant-missing` 保持休眠。启用开关打开后，透明 PNG 必须真实存在并
通过完整质量与轮廓差异门槛。这样未完成的美术计划不会成为静默悬空引用。

`pixelSize` 是经过审阅的母版实际尺寸，任何替换都会触发漂移检查。`minimumSize` 是该姿态的最低可接受分辨率；换入更高分辨率版本时，应同时更新 `pixelSize`、QML 比例和审阅记录，不能靠缩放掩盖低清素材。

新素材必须使用 `resolutionTier: "production-v1"`，其 `minimumSize` 同时满足：短边至少 512px、长边至少 1024px、总像素至少 524288。当前四张低分辨率运行时素材被逐 key 固定为不可扩展的 `legacy-v1` 基线；只有这四个既有 key 能使用该档位，尺寸或文件发生变化就必须重新审阅。新增姿态不能把自己标成 legacy 来绕过生产分辨率。

## 自动拒绝条件

运行 `scripts/verify_pose_assets.py` 时会拒绝：

- 不是 PNG、不是原生 `RGBA` 四通道，或 alpha 不同时包含 `0` 与 `255`；
- 透明像素比例、实体覆盖不足，四角没有达到清单要求的透明比例；
- alpha 为零的像素仍残留明显 RGB，存在缩放采样污染风险；
- 半透明/轮廓区域的饱和绿色比例或白色蒙版边风险超过阈值；
- 文件低于最低尺寸，实际尺寸与 `pixelSize` 不同，或新素材没有达到 production-v1 的 512×1024 / 524288px 基线；
- 清单比例、实际像素比例与 `V03PetBody.qml` 的比例任一不一致；
- 绝对路径、越过主题目录的路径，以及带 `concept`、`generated`、`checkerboard` 或 `chroma` 标记的概念/绿幕素材路径；
- pose bundle、素材 key、规格和 QML 比例没有形成一对一闭环。
- 独立姿态缺少合法点击轮廓，或休眠可选姿态缺少任一完整声明；

RGB 棋盘格即使视觉上像“透明背景”也会被 `RGBA`、alpha 范围、透明角和透明比例四重门槛拒绝。RGBA 文件若只是把棋盘格留在不透明层，同样无法通过透明角与透明比例检查。

## 运行方式

在项目根目录使用正式 Python 环境执行：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'F:\code\Lilies in the box\.venv\Scripts\python.exe' scripts\verify_pose_assets.py
```

命令以非零退出码拒绝素材，并写出机器可读的 `artifacts/pose-asset-gate.json`。完整 `pytest` 会自动运行同一门槛；负向测试还会构造 RGB 棋盘格、隐藏 RGB、绿边、白边、低分辨率、比例漂移和概念路径，防止验证器只会对现有素材“报绿”。

## 当前正式基线

当前允许进入运行时的六个 asset key 为：

- `posePerchProne`
- `poseTitleSit`
- `poseEdgePeek`
- `poseListeningLive`
- `poseFocusKneel`
- `poseExpansionSheet`（阅读、展示、托盒、休息四个自定义 sprite）

以下五个生产契约已经建立，但因为透明母版尚未通过门槛，运行时保持休眠并
使用支持全部服装的分层姿态：

- `poseMicroCornerGripV1`
- `poseWindowProneV2`
- `poseWindowDangleV1`
- `poseWideWindowSprawlV1`
- `poseEdgeLeanV1`

`art-reference/generated-v0.3` 下的概念图仍只用于姿态设计参考，不属于正式素材。

## Runtime presentation readiness (v0.3.29)

Pose artwork uses two persistent `Image` slots. The committed Ready slot stays
visible while the inactive slot decodes a new source or sprite clip. A
cross-fade starts only after the inactive slot reports `Image.Ready`. An
`Image.Error` result converges to the layered outfit renderer; a failed or
still-loading target must never replace the committed frame or leave a
cord/shadow-only window.

The QML and native hit tests both call `containsCharacterPoint()`. At transition
progress zero only the outgoing mask is active. Once the incoming layer has
non-zero opacity, hit testing uses the union of the two visible masks; after the
fade it uses only the committed incoming mask. The off-screen gate checks this
contract on a 19 by 19 grid, including a missing target, eight rapid pose
changes, and layered failure fallback for all six outfits.
