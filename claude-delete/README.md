# claude-delete

在 Claude Code 终端主会话中输入 `/delete`，直接退出并永久删除当前本地会话，无需填写 ID，也不二次确认。项目源码不受影响。

## 安装

要求 macOS、Python 3.9+、Claude Code。当前已验证 Claude Code 2.1.280 的原生安装版本；其它系统和版本未验证。

在仓库根目录运行：

```sh
./claude-delete/install.sh
```

安装器默认使用 `~/.claude`，支持 `CLAUDE_CONFIG_DIR`，也可显式指定：

```sh
./claude-delete/install.sh --config-dir '/path/to/claude-config'
```

安装器复制运行文件到配置目录，不依赖仓库的绝对路径，安装后可以移动仓库。它会合并 settings.json，保留其它设置和 Hook；重复安装不会重复注册 Hook。修改前的文件会备份到配置目录下的 `claude-delete-backups/<时间>/`。目标文件内容不同时默认拒绝覆盖，检查后可通过 `--force` 备份并覆盖。

安装文件：

```text
~/.claude/
├── commands/delete.md
├── hooks/delete_session.py
└── settings.json          # 新增 UserPromptSubmit Hook
```

**安装后退出并重新启动 Claude Code**，然后输入：

```text
/delete
```

直接运行 `python3 claude-delete/install.py` 也可安装；安装器将当前 Python 解释器的绝对路径写入 Hook 配置，解释器被移动或删除后需要重新安装。

## 实现

`delete.md` 提供斜杠命令入口。`UserPromptSubmit` Hook 精确匹配 `/delete`，从事件读取 `session_id`、`transcript_path` 并校验路径与 ID，阻止指令发送给模型。普通提示词直接放行。

脚本沿父进程链识别调用它的 Claude Code，启动独立清理进程，向该 Claude 进程发送 SIGTERM。清理进程等待 Claude 完全退出后再删除记录，避免退出时重新写回。60 秒内未退出则保留会话文件并记录失败。

清理范围仅包含当前 ID 对应的：

- `projects/<项目>/<ID>.jsonl` 与同目录 `<ID>/` 附件目录；
- `file-history/<ID>/`、`session-env/<ID>/`、`tasks/<ID>/`；
- `history.jsonl` 中 `sessionId` 相同的行；
- 当前项目旧版 `sessions-index.json` 中 `sessionId` 相同的条目。

清理日志位于配置目录下的 `delete-session-logs/<ID>.log`，仅记录 ID、结果或异常，不备份对话正文。退出时 Claude 仍可能打印自身的 resume 提示，但该记录随后被删除，通常不足一秒。退出码可能是 143（SIGTERM）。

## 边界

不支持云端、Claude Desktop 专属会话；不回退代码，也不承诺清除所有全局缓存、日志或其它工具生成的会话备份。不要同时在另一终端恢复同一个 session。

共享历史采用写入前版本检查与重试。Claude Code 本身不配合加锁，多个进程同时写入共享历史仍存在很短的竞争窗口；高并发写入场景未验证。旧索引格式未知时保留索引并记录错误，前面已经完成的删除不会回滚。

Hook 被禁用、使用 safe mode 或被其它配置覆盖时功能不会生效。命令文件包含失败兜底指令，不授权模型自行删除或终止进程。

## 卸载

```sh
./claude-delete/install.sh --uninstall
# 如安装时指定了配置目录，卸载时也要指定同一个目录：
./claude-delete/install.sh --config-dir '/path/to/claude-config' --uninstall
```

卸载只删除工具文件及引用该脚本的 Hook，保留其它配置、备份和清理日志。卸载不会恢复被删除的会话。若工具文件被手动修改，卸载前也会要求检查，或使用 `--force` 备份后卸载。完成后重启 Claude Code。

## 验证

```sh
python3 -m unittest discover -s claude-delete/tests -v
sh -n claude-delete/install.sh
```

自动测试使用临时配置，覆盖安装、重复安装、卸载、其它 Hook 保留、冲突备份、带空格路径、清理范围、退出等待及超时保护，不删除真实会话。此前已在 macOS / Claude Code 2.1.280 的隔离配置中验证真实 CLI 与交互终端 `/delete` 流程。

官方机制说明：[自定义命令](https://code.claude.com/docs/en/skills)、[UserPromptSubmit Hook](https://code.claude.com/docs/en/hooks#userpromptsubmit)。
