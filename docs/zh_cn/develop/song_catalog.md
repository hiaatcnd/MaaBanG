# 中国服歌曲目录与难度联动

来源：[Bestdori 歌曲目录](https://bestdori.com/info/songs)，批量接口为 [songs/all.7.json](https://bestdori.com/api/songs/all.7.json) 与 [bands/all.1.json](https://bestdori.com/api/bands/all.1.json)。只下载歌曲和乐队元数据，不逐首抓页面，不下载音频或谱面文件。

## 文件与刷新

- `agent/data/songs_cn.json`：离线目录，包含 ID、中国服名称、其他区服名称别名、演奏乐队、分类、长度、发行/关闭时间、各难度等级及音符数、封面资源名和来源链接。
- `docs/data/songs_cn.csv`：可阅读的目录表格。
- `tools/update_song_catalog.py`：请求两个批量接口，刷新上述文件及 `assets/interface.json` 的歌曲和联动难度选项。

```powershell
python tools/update_song_catalog.py
```

运行游戏任务不依赖联网。获取失败或中国服筛选结果为空会报错，不用空列表覆盖现有目录。需要审查更新差异后再提交。

## 中国服筛选

Bestdori 前端的区服顺序为 `jp, en, tw, cn, kr`，中国服索引是 3。以中国服 `publishedAt` 不为空且已到发布时间为收录条件。中国服 `closedAt` 已到期的歌曲保留归档，但不提供自动演出选项。

各难度单独检查中国服发行时间：没有 `publishedAt` 覆盖字段则沿用歌曲日期；存在该字段但中国服值为空时，不借用日服日期，暂不提供该难度。选歌列表要求 EXPERT 数据可用，以保证进入收藏前可使用 EXPERT 清除 Special 筛选。原始意义不明确的数据需要复核，不能直接认定游戏中不存在该谱面。

当前快照收录 747 首已发布歌曲，735 首未关闭；其中 734 首满足选歌条件，134 首具有中国服已开放的 SPECIAL。`Second to None`（ID 690）歌曲已发布，但四个基础难度的中国服日期均为空，因此暂不提供选歌候选。准确抓取时间在 JSON 的 `fetched_at`。

## 界面与运行

歌曲 case 引用对应的子难度配置，切换歌曲时只显示该歌开放的难度。EXIST 仅 Easy–Expert，SAVIOR OF SONG 包含 Special。保留旧的难度配置定义兼容已有配置，任务顶层不再显示独立的全难度选择器。旧配置仍指定不可用 Special 时，Agent 回退 Expert。

同名歌曲保留独立 ID，界面显示演奏乐队；运行时额外识别选歌页的乐队名称。命令行可用 `--song 676` 选择明确版本，不接受不带 ID 的歧义歌名。

目录代替手工录入歌单，游戏内定位仍需 OCR，并且仍只查找收藏。原有 SAVIOR OF SONG、EXIST 的完整自动演出已经实机验证；新增歌曲及同名版本尚未逐首实机验证，长标题、特殊字符或乐队 OCR 无法确认时会停止，不会猜选。
