# Issue Tracker：GitHub

本仓库的 Issue 和 PRD/规格统一存放在 GitHub Issues，并使用 `gh` CLI 操作。

## 约定

- 仓库身份从当前目录的 Git remote 推断。
- “发布到 issue tracker”表示创建 GitHub Issue。
- 创建、读取、评论、添加或移除标签、关闭 Issue 均使用 `gh issue`。
- 多行 Issue 正文通过临时文件或标准输入安全传递，不把正文拼入 shell 命令。
- Pull Request 不作为需求 triage 的入口。

## 常用操作

- 创建：`gh issue create --title "..." --body-file <file>`
- 读取：`gh issue view <number> --comments`
- 列表：`gh issue list --state open --json number,title,body,labels,comments`
- 评论：`gh issue comment <number> --body "..."`
- 标签：`gh issue edit <number> --add-label "..."`
- 关闭：`gh issue close <number> --comment "..."`
