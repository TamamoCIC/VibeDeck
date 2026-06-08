# Terminal vs Layout 架构混淆

**状态**: 已修复 (2026-06-08)  
**优先级**: 高  
**发现日期**: 2026-06-08

## 问题描述

当前 VibeDeck 的架构将 **Terminal（终端设备）** 和 **Layout（布局）** 混淆为同一个概念。

### 现状

```
Terminal "default" (4x8)   ← 被当成独立终端
Terminal "f857..." (3x5)   ← 被当成独立终端
```

Web UI 的下拉框选择 "2×3 / 3×5 / 4×8" 切换的是终端（不同 grid），
但用户直觉上认为这是切换布局。

### 正确语义

| 概念 | 含义 | 关系 |
|------|------|------|
| **Terminal** | 物理或虚拟设备（Stream Deck、手机、平板） | 1 个设备 = 1 个 Terminal |
| **Layout** | 该设备上的 Widget 排布 + 外观 | 1 个 Terminal 可有多个 Layout |
| **Grid** | 设备的物理按键排列（行×列） | Terminal 的属性，不可变 |

### 导致的问题

1. **Hook 事件路由错误**：所有 WIDGET_STATE_UPDATE 默认发到 `"default"` 终端，
   用户连接的 `"My Terminal"` 收不到更新
2. **状态重复**：每个 Terminal 维护独立的 Widget 副本，同一 Agent 的状态被复制多份
3. **Web UI 困惑**：下拉框标为 "4×8 / 3×5 / 2×3" 但实际切换的是终端而非布局
4. **布局存取无意义**：保存布局时只保存了一个 Terminal 的 Frame，加载时也只影响一个 Terminal

### 修复历史（补丁）

三次修复了同一个 Terminal 路由问题：
- `5169a76` Thinking 计时器遍历所有终端
- `4b021cc` Thinking 强制帧推送到所有终端
- `f76c346` Widget 更新广播到所有终端

这些都是补丁，未解决根本架构问题。

## 建议方案

### 重构目标

```
Terminal Registry
├── Terminal "My Phone" (grid: 3x5, token: xxx)
│   ├── Layout "Claude Monitor" (active)
│   └── Layout "Full Dashboard"
├── Terminal "Stream Deck XL" (grid: 4x8, token: yyy)
│   └── Layout "Default" (active)
```

### 改动范围

1. **config.yaml** — terminals 增加 `active_layout` 字段
2. **types.py** — LayoutFrame 与 Terminal 解耦
3. **terminal_registry.py** — 管理 Terminal→Layout 映射
4. **layout.py / LayoutEngine** — 按 Terminal 管理多个 Layout
5. **event_loop.py** — 移除所有 "遍历所有终端" 补丁，改为按 Terminal→active_layout 路由
6. **web/server.py** — API 按 terminal_id 读写 layout
7. **web/static/index.html** — 下拉框改为 "布局切换" 语义
