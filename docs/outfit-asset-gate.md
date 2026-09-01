# 服装素材接入门槛

这套门槛只审计服装素材和运行时换装契约，不生成、不抠图，也不修补位图。未通过的素材只能留在参考区或明确标成别名，不能伪装成已完成的独立服装。

## 清单规则

`themes/first-encounter/theme.json` 的 `character` 必须同时声明：

- `outfitAssetGateVersion: 1`；
- 完整且同名的 `outfits`、`outfitBundles` 与 `outfitArtworkSpecs`；
- 每个 bundle 和 spec 使用当前 `outfitAlignment.anchorVersion`；
- 每个 spec 固定 asset key、实际像素、最低像素、宽高比、SHA-256、实体中心与脚线；
- `implementationStatus` 只能是 `production` 或 `visual-alias`；
- 相同 SHA-256 只能有一个 `production` 主体，其余必须显式指向该主体。

“清凉棉质连衣裙”目前与“初遇裂纹裙”指向同一张已审阅 PNG，因此清单明确记录为 `visual-alias`，不是独立完成的第六张服装图。当前是 6 个可选服装 ID、5 张独立位图。

## 位图规则

服装没有 legacy 豁免，全部必须使用 `production-v1`。共享的生产下限为短边至少 512px、长边至少 1024px、总面积至少 524288px；本主题还把每张服装的 `minimumSize` 提高到至少 900×1600。

自动门槛会拒绝：

- 非 PNG、非原生 RGBA，或 alpha 未同时包含 0 和 255；
- 四角不透明、透明面积或实体面积不足；
- 完全透明像素中残留 RGB；
- 半透明轮廓存在超标绿边或白色蒙版边风险；
- 文件尺寸、清单尺寸、清单比例或 QML 比例不一致；
- 绝对路径、逃出主题目录的路径，或概念图、生成图、棋盘格、绿幕路径；
- 未经审阅就替换文件（SHA-256 漂移）；
- 重复图片未声明视觉别名，或别名与目标哈希并不相同。

素材门槛复用 `verify_pose_assets.py` 的真 RGBA、透明角、隐藏 RGB、绿边/白边与分辨率检查。该共享检查已有 RGB 棋盘格、隐藏 RGB、绿边、白边和低分辨率负向测试；服装测试另外覆盖重复哈希、错误别名、锚点漂移、比例漂移和三切片错位。

## 锚点契约 v2

`first-encounter` 是基准服装。运行时保留稳定的 `figureFrame`、绳端和公开命中边界，并完成以下校正：

1. `figureFrame` 使用当前素材的精确宽高比，避免 `Image.Stretch` 横向压缩；
2. 以 alpha ≥ 192 的实体包围盒计算人物中心和脚线；
3. 将每套服装的实体中心和脚线对齐到“初遇”基准；
4. 头发/头部、肩膀/双手、裙摆三个呼吸切片使用完全相同的图像空间偏移；
5. 支撑红绳使用独立的归一化锚点，不随服装画布尺寸漂移。

新增或替换服装若改变这些语义，必须统一提升 `anchorVersion`，更新清单、QML 精确分数、SHA-256，并重新审阅。

## 离屏验证

在候选项目根目录运行：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QSG_RHI_BACKEND = 'software'
& .\.venv\Scripts\python.exe scripts\verify_outfit_assets.py
& .\.venv\Scripts\python.exe scripts\verify_outfit_ui.py
& .\.venv\Scripts\python.exe -m pytest -q tests\test_outfit_asset_gate.py
```

第一条生成 `artifacts/outfit-asset-gate.json`；第二条只用 Qt 离屏后端逐套换装，验证资源来源、精确比例、脚线、人物中心、三切片连续性和视觉别名，并生成 `artifacts/outfit-runtime-gate.json`。两条命令都不会打开桌面窗口或改动位图。
