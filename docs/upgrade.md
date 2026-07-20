# 升级指南 · Upgrade guide

## 升级应用

```
pipx upgrade infinance          # 发布包
# 或源码：
git pull && uv sync && npm ci --prefix frontend && npm run build --prefix frontend
```

## 数据库会自动迁移

启动（或任何命令首次连库）时，数据库按版本链自动升级到当前 schema：

- 迁移**之前**会在库文件旁自动留一份备份：`infinance.db.v<旧版本>.bak`；
- 整条迁移在单个事务里执行 — 中途失败会回滚到旧版本，不会留下半迁移状态；
- 回退方法：停止服务，用 `.bak` 文件覆盖 `infinance.db`，装回旧版本应用。

无需手动操作；`infinance doctor` 会显示当前 schema 版本。

## MediaCrawler（vendor）重新锁定

infinance 把 MediaCrawler 锁定在一个具体 commit（`VENDOR_PIN`，见
`infinance/providers/mediacrawler.py`）。升级 infinance 后：

```
infinance setup     # 幂等：会 fetch 并 checkout 新的锁定版本
infinance smoke     # 3 帖最小抓取验证
```

集成面刻意很窄：CLI 参数、JSONL 字段名、若干补丁锚点。上游若重命名了锚点，补丁器会**硬失败并明确报错**（而不是带错误配置继续爬）— 这种情况等 infinance 更新，不要手改 vendor。

## 升级后检查单

1. `infinance doctor` 全绿；
2. `infinance smoke` 能抓到数据；
3. 打开看板确认历史数据仍在（迁移不动数据）。
