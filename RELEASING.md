# 发布说明

作者：昭璃。公开仓库：[Earendel-xr/astrbot_deepseek_search](https://github.com/Earendel-xr/astrbot_deepseek_search)。

AstrBot 内部插件标识仍为 `astrbot_plugin_deepseek_search`，避免影响已有配置文件和工具权限。仓库名与插件标识可以不同；安装 ZIP 的顶层目录固定使用内部标识。

## 仓库与自动化

- 仓库只包含本插件的源码、测试与文档，不包含 AstrBot 实例、聊天数据、部署脚本或本地凭据。
- `metadata.yaml` 的 `repo` 指向上述仓库。内部插件标识与安装包目录保持稳定。
- 推送代码后检查 [GitHub Actions](https://github.com/Earendel-xr/astrbot_deepseek_search/actions) 的测试与构建结果。工作流不需要 API Key，默认只有读取仓库的权限。

## 每次发版

1. 更新 `metadata.yaml` 的 `version` 和 `CHANGELOG.md`。
2. 运行测试；在支持 AstrBot 的环境中确认集成测试没有被跳过。
3. 在插件目录执行 `python build.py --expected-version v0.2.0`（版本参数替换为本次版本）。
4. 校验生成的 ZIP 和同名 `.zip.sha256`；创建与元数据版本一致的 Git 标签。
5. 在 GitHub 创建 Release，上传构建产物。CI 只上传 Actions artifact，不自动发布 Release。

打包输出位于插件父目录的 `dist/`，也可用 `--output-dir` 指定其他目录。文件名和包内目录来自元数据，不依赖本地克隆目录名称；同一份文件内容的 ZIP 可重复构建得到相同摘要。

## v0.1.0 升级到 v0.2.0

- 保留内部插件标识、工具名称和旧配置键；升级时保留原配置与密钥即可。
- 新选项未配置时使用默认值：8 MiB 缓存、每用户每分钟 10 次、每群每分钟 30 次、同类工具 1 秒冷却。
- 限额会约束管理员、指令和工具调用。需要原先不限制频率的行为时，将两个每分钟限额和冷却设为 0。
- 输出默认改为每段最多 1800 字符。文件输出为可选功能，需要消息平台支持。
- 安装后保存配置并重载插件，再检查人格的工具列表。工具开关关闭后不再注册相应工具；原有工具名称未变。
- v0.1.0 与 v0.2.0 均已验证真实接口及模型工具调用；其他平台、模型及插件组合仍应在升级后确认实际表现。
