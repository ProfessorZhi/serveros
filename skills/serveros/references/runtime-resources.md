# 长命资源与 owner

操作长命资源前读 `serverosctl runtime status <id>`；需要原因再 `serverosctl runtime explain <id>`。
跨资源整理才读 `serverosctl runtime hygiene`，并使用 `runtime-management`。
owner 依据 registry 或显式 `serveros.owner=<type>:<id>`，不把 compose 路径标签当 owner。
未知 owner/消费者 = DEFER；protected、ACTIVE、resumable waiting、active lease、`LIT-0103`、`LIT-0104`
及本机政策指定的资源不可擅动。清理、搬迁、重命名前重新取活动快照，清理须显式批准。
process/transport 存在不代表健康；`functional: UNKNOWN` 不得写成 HEALTHY。
