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

候选版的“清凉棉质连衣裙”已接入独立的 `lilith-outfit-summer-cotton-transparent-v1.png`：高圆领、宽肩带、无袖、轻褶棉裙，不再与初遇裙共用图片。当前候选是 6 个服装 ID、6 张独立位图；已安装的 v0.3.56 仍保留旧别名，未随这次素材接入替换。

新图由内置 imagegen 在原人物上只编辑服装，保留生成的 941×1672 RGBA 像素和 alpha，没有后期抠图、放大或重写透明边缘。完整提示词见 `summer-outfit-v1-prompt.txt`。三处全透明像素含隐藏 RGB，比例约 0.00000336，在既有 0.0005 门槛以内；原门槛未放宽。实体中心不变，脚线以实际 alpha≥192 的边界重新登记为 1602/1672，运行时仍对齐初遇基准。

旧的烘焙姿态画的是初遇裙，不再把新棉裙列入其 `artworkOutfits`。新棉裙仍兼容同样的动作与栖息状态，使用保留本套服装的分层渲染；直到有对应母版前，不宣称它有独立的烘焙趴姿。离屏门槛检查六套来源、比例、脚线、裁片对齐与阅读不换衣，并生成深浅背景预览。

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
