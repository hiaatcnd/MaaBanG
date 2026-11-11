# MaaBanG 整包更新

在设置的版本更新页面使用“检查 MaaBanG 更新”或“更新 MaaBanG（整包）”。开启“自动更新 MaaBanG（完成后重启）”后，启动时自动下载并安装更新；默认仅检查版本，不自动安装。下载源为项目的 GitHub Releases，沿用界面的代理、GitHub Token 与更新渠道设置。

首次接入需要手动下载包含此功能的完整安装包。旧版的更新器不能识别新的整包布局，不能依靠旧版按钮完成接入。本地拖入更新包也可使用同一更新流程，但包必须包含 `package-manifest.json`。

## 更新过程

1. 下载完整 Windows x64 包，验证 GitHub Release 资产提供的 SHA256；缺少校验值或不匹配时停止。手动选择本地包没有远程摘要，仍须通过包内逐文件清单校验。
2. 在安装目录旁的 `.maabang-update-随机编号` 中解压并验证包结构、产品、版本、必需文件和每个文件的 SHA256。拒绝路径越界、重复路径、符号链接和清单外文件。
3. 停止任务与 Agent，保存配置并退出界面。独立更新进程等待旧界面退出，保留旧安装目录为暂存目录内的 `previous`，将已校验的新目录移到原安装路径。
4. 使用新启动器运行 `--check-agent`，检查内置 Python、框架 DLL 一致性、OCR 资源和 Agent 通信，通过后从 `MaaBanG.exe` 重启。此检查不会连接模拟器或消耗游戏资源。
5. 替换或启动检查失败时，恢复旧目录；失败的新包保留为 `failed`。错误写入暂存目录中的 `update-error.log`，成功或回退状态写入 `status.txt`。

### 安装目录被 ADB 占用

ADB 后台服务可能继承界面或 Agent 的工作目录。即使 MaaBanG 已退出，只要该服务仍把安装目录内的文件夹作为工作目录，Windows 就会阻止整包目录搬移，并可能报告“访问被拒绝”。这不等同于目录权限不足。

更新器在搬移受阻时，只对确认占用当前安装目录的、同用户的本机 ADB 后台服务尝试恢复：核对进程身份、工作目录句柄、后台服务参数和监听端口归属，再通过该连接请求 ADB 正常退出并等待确认。不会终止其他进程，也不会对所有 ADB 服务统一发送停止命令。使用同一服务的其他 ADB 客户端会短暂断开，后续连接会重新启动服务。

无法确认占用来源、服务未正常退出或目录仍被其他程序占用时，更新器保留原安装和已校验的新包，并记录失败步骤。不要仅因“访问被拒绝”就修改目录权限或合并新旧包。旧版更新器尚未包含这项恢复能力时，需要先解除已确认的占用，再重试更新。

共享配置和谱面缓存仍在 `%USERPROFILE%\.maabang`，整个更新过程不会覆盖它们。使用 `MAABANG_DATA_DIR` 时，请将其放在安装目录之外；更新器会拒绝数据与安装目录重叠的布局。旧安装目录中的额外文件保留在 `previous`，不会自动混入新包。

安装目录和其父目录须可写，磁盘应容纳旧包、新包及下载 ZIP。旧包备份不会自动删除；确认新版正常后，可手动清理对应的 `.maabang-update-*` 目录及目录旁的 `.安装目录名.update.lock` 空文件（程序未在更新时）。如果电脑恰在目录切换时断电，自动回退可能无法执行：确认 MaaBanG 和更新器都已退出后，可将 `previous` 目录移回原安装路径。不要合并两套版本的文件。

界面和 MaaFramework 的独立更新入口统一转到 MaaBanG 整包更新，以保持定制界面、Python binding 与原生框架版本匹配。

## 开发验证

```powershell
python -m unittest discover -s tests -p test_package_update.py -v
python tools/verify_package_update.py dist/MaaBanG-v版本号-win-x64.zip --work install/update-verification
```

第二条会在全新隔离目录中执行真实包替换和新包 Agent 检查，保留 `report.json`；可通过 `--old-package` 指定旧 ZIP。它不会打开游戏或修改日常配置，也不代替正式发布后的联网下载、界面操作与重启验证。

Windows 更新器回归测试还会在隔离目录中创建持有安装内工作目录的模拟 ADB 服务，覆盖 32 位和 64 位进程、无关进程保护、释放失败及更新回退。模拟服务只使用本机测试端口，不连接模拟器。

发布清单由 `tools/package_manifest.py` 在构建检查结束、清理缓存后生成。整包更新器为 `tools/MaaBanGUpdater.cs`，界面桥接代码为 `tools/MaaBanGUpdate.cs`，上游集成补丁为 `tools/patches/mfaa-package-update.patch`。构建依赖的固定版本与哈希仍由 `tools/release-inputs.json` 和 Python 发布清单管理。
